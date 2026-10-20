#include "app.h"
#include "config.h"
#include "pins.h"
#include "imu.h"
#include "sync.h"
#include "led.h"
#include "rec.h"
#include "sdlog.h"
#include "stream.h"
#include "power.h"
#include "netlog.h"
#include "wifi.h"
#include "settings.h"

#include <stdio.h>
#include <string.h>
#include <stdarg.h>
#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_random.h"
#include "esp_system.h"
#include "esp_app_desc.h"
#include "esp_ota_ops.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"

static const char *TAG = "app";

static const char *const MODE_NAMES[MODE_COUNT] = { "idle", "check", "selftest", "run", "flicker" };

const char *app_mode_name(uint8_t mode) { return mode < MODE_COUNT ? MODE_NAMES[mode] : "?"; }

// ---------------------------------------------------------------- shared state department
// Written by the controller, read by the HTTP task: copy under s_mux.
typedef struct {
    app_mode_t mode;
    uint16_t   ok_mask;          // sensors that answered WHO_AM_I at the last init
    int        vbat_mv, vbus_mv;
    char       last_error[80];
    // run
    char       run_file[20];
    char       run_label[64];
    uint16_t   run_mask;
    bool       run_sd;
    int64_t    run_t0_us;
    uint32_t   run_fifo_ovr;
    uint32_t   run_records;
    int        flicker_hz;
} app_status_t;

static portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;
static app_status_t s_st;
static char s_selftest[2048] = "{\"done\":false}";
static char s_tmp[sizeof s_selftest];   // built by the controller, then published under s_mux

static void publish_selftest(const char *src)
{
    taskENTER_CRITICAL(&s_mux);
    memcpy(s_selftest, src, sizeof s_selftest);
    taskEXIT_CRITICAL(&s_mux);
}
static QueueHandle_t s_q;

static void st_update(void (*fn)(app_status_t *, const void *), const void *arg)
{
    taskENTER_CRITICAL(&s_mux);
    fn(&s_st, arg);
    taskEXIT_CRITICAL(&s_mux);
}
static void set_mode_fn(app_status_t *s, const void *a) { s->mode = *(const app_mode_t *)a; }

static void set_error(const char *fmt, ...)
{
    char b[sizeof s_st.last_error];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(b, sizeof b, fmt, ap);
    va_end(ap);
    ESP_LOGE(TAG, "%s", b);
    taskENTER_CRITICAL(&s_mux);
    memcpy(s_st.last_error, b, sizeof b);
    taskEXIT_CRITICAL(&s_mux);
}

app_mode_t app_mode(void) { return s_st.mode; }
bool app_is_running(void) { return s_st.mode == MODE_RUN; }

esp_err_t app_request(const app_cmd_t *c)
{
    if (!s_q) return ESP_ERR_INVALID_STATE;
    return xQueueSend(s_q, c, pdMS_TO_TICKS(50)) == pdTRUE ? ESP_OK : ESP_ERR_TIMEOUT;
}

static const char *uname(int i)
{
    static char b[16];
    snprintf(b, sizeof b, "U%d", i + 1);
    return b;
}

// ---------------------------------------------------------------- sensor helpers department
static uint16_t init_sensors(uint16_t mask)
{
    uint16_t ok = 0;
    for (int i = 0; i < NUM_IMU; i++)
    {
        if ((mask & (1u << i)) && imu_init(i)) ok |= 1u << i;
    }
    return ok;
}

static void power_down_mask(uint16_t mask)
{
    for (int i = 0; i < NUM_IMU; i++)
        if (mask & (1u << i)) imu_power_down(i);
}

// Switch every sensor in `mask` into ODR-triggered mode inside ONE reference period: wait for
// a sync edge, then arm all of them (~3 ms total, the next edge is 100 ms away). They then
// start on the same edge, which the host's timing reconstruction relies on (and checks).
static void arm_after_edge(uint16_t mask, bool log_arm)
{
    uint32_t e = edge_count(EDGE_SYNC);
    int64_t give_up = esp_timer_get_time() + 300000;
    while (edge_count(EDGE_SYNC) == e && esp_timer_get_time() < give_up) vTaskDelay(1);
    if (edge_count(EDGE_SYNC) == e) set_error("no IMU_SYNC edge before arming (LEDC/ISR?)");
    for (int i = 0; i < NUM_IMU; i++)
    {
        if (!(mask & (1u << i))) continue;
        if (i == IMU_CENTRE) imu_enable_drdy_int1(i);   // before the ODR starts: catch pulse 0
        imu_start_triggered(i);
        if (log_arm)
        {
            rec_hdr_t h = { .type = REC_ARM, .sensor = (uint8_t)i, .t_us = esp_timer_get_time() };
            rec_put(&h, NULL);
        }
    }
}

typedef struct { uint32_t xl, gy, ts, other, ovr; } fifo_count_t;

static uint8_t s_buf[140 * FIFO_WORD_BYTES];

// Empty sensor i's FIFO. log: emit REC_FIFO records. cnt: count words by tag (may be NULL).
static void drain_sensor(int i, bool log, uint32_t *seq, fifo_count_t *cnt)
{
    int64_t t = esp_timer_get_time();          // BEFORE the status read: see REC_FIFO in rec.h
    uint8_t st2 = 0;
    int lvl = imu_fifo_status(i, &st2);
    if (lvl < 0) return;
    if (st2 & FIFO_ST2_OVR_LATCHED)
    {
        if (cnt) cnt->ovr++;
        if (log)
        {
            rec_hdr_t h = { .type = REC_FIFOST, .sensor = (uint8_t)i, .seq = st2, .t_us = t };
            rec_put(&h, NULL);
        }
    }
    while (lvl > 0)
    {
        int n = lvl > 140 ? 140 : lvl;
        if (imu_fifo_read(i, s_buf, n) != ESP_OK) break;
        if (log)
        {
            rec_hdr_t h = { .type = REC_FIFO, .sensor = (uint8_t)i,
                            .nbytes = (uint16_t)(n * FIFO_WORD_BYTES), .seq = (*seq)++, .t_us = t };
            rec_put(&h, s_buf);
        }
        if (cnt)
        {
            for (int m = 0; m < n; m++)
            {
                int tag = s_buf[m * FIFO_WORD_BYTES] >> 3;
                if (tag == TAG_XL_NC) cnt->xl++;
                else if (tag == TAG_GY_NC) cnt->gy++;
                else if (tag == TAG_TIMESTAMP) cnt->ts++;
                else cnt->other++;
            }
        }
        lvl -= n;
    }
}

static void emit_config(int64_t now)
{
    uint8_t buf[REC_FILE_HDR_BYTES + 160];
    rec_file_header(buf);
    int n = snprintf((char *)buf + REC_FILE_HDR_BYTES, sizeof buf - REC_FILE_HDR_BYTES,
                     "fw=%s ref_hz=%d nodr=%d samples_per_ref=%d odr=0x%X bdr=0x%X haodr=%d "
                     "fs_xl=%d fs_g=%d ts_dec=%d mask=0x%04X label=%s",
                     esp_app_get_description()->version, REF_HZ, ODR_TRIG_NODR, SAMPLES_PER_REF,
                     TRIG_ODR_CODE, FIFO_BDR_CODE, HAODR_SEL, FS_XL_CODE, FS_G_CODE, FIFO_TS_DEC,
                     s_st.run_mask, s_st.run_label);
    if (n < 0) n = 0;
    if (n > (int)(sizeof buf - REC_FILE_HDR_BYTES - 1)) n = sizeof buf - REC_FILE_HDR_BYTES - 1;
    rec_hdr_t h = { .type = REC_CONFIG, .nbytes = (uint16_t)(REC_FILE_HDR_BYTES + n), .t_us = now };
    rec_put(&h, buf);
}

// ---------------------------------------------------------------- status LED department
static int s_blink_ticks = -1;   // >= 0 while a "blink the mode number" sequence is playing
static int s_blink_n;

static void status_led(app_mode_t m, int64_t now)
{
    bool on;
    if (s_blink_ticks >= 0)
    {
        int t = s_blink_ticks++;                 // 4 ticks off, then n x (2 on, 2 off)
        on = t >= 4 && ((t - 4) / 2) % 2 == 0;
        if (t >= 4 + 4 * s_blink_n) s_blink_ticks = -1;
    }
    else
    {
        int ms = (int)((now / 1000) % 2000);
        switch (m)
        {
        case MODE_IDLE:     on = ms < 100; break;                 // short blip every 2 s
        case MODE_RUN:      on = ms < 1000; break;                // 1 s on / 1 s off
        case MODE_FLICKER:  on = true; break;
        default:            on = (ms % 500) < 250; break;         // check/selftest: 2 Hz
        }
    }
    gpio_set_level(PIN_STATUS, on);
}

// ---------------------------------------------------------------- mode: check (stage 1)
static uint16_t s_check_mask;
static int s_check_tick;

static esp_err_t check_enter(void)
{
    netlog_printf("[CHECK] WHO_AM_I + free-running readout. Board face up: az ~ +1 g.\n");
    s_check_mask = init_sensors(MASK_ALL);
    for (int i = 0; i < NUM_IMU; i++)
        if (s_check_mask & (1u << i)) imu_start_free_running(i);
    netlog_printf("[CHECK] %d / %d sensors answered\n", __builtin_popcount(s_check_mask), NUM_IMU);
    s_check_tick = 0;
    return ESP_OK;
}

static void check_tick(void)
{
    if (s_check_tick++ % 10) return;   // once a second
    netlog_printf("\n sensor |     ax(g)     ay(g)     az(g) |     gx(dps)     gy(dps)     gz(dps)\n");
    for (int i = 0; i < NUM_IMU; i++)
    {
        if (!(s_check_mask & (1u << i)))
        {
            netlog_printf(" %-5s | no response\n", uname(i));
            continue;
        }
        uint8_t b[12];
        imu_read(i, R_OUTX_L_G, b, 12);
        netlog_printf(" %-5s |   %7.3f   %7.3f   %7.3f |   %8.2f   %8.2f   %8.2f\n", uname(i),
                      le16(b + 6) * XL_G_PER_LSB, le16(b + 8) * XL_G_PER_LSB, le16(b + 10) * XL_G_PER_LSB,
                      le16(b) * G_DPS_PER_LSB, le16(b + 2) * G_DPS_PER_LSB, le16(b + 4) * G_DPS_PER_LSB);
    }
}

static void check_exit(void) { power_down_mask(s_check_mask); }

// ---------------------------------------------------------------- mode: selftest (stage 2)
enum { ST_WARM, ST_SETTLE, ST_MEASURE };
static struct {
    int phase;
    int64_t t_phase, t0;
    uint32_t e0;
    uint16_t mask;
    fifo_count_t c[NUM_IMU];
} s_t2;

static esp_err_t selftest_enter(void)
{
    netlog_printf("[SELFTEST] ODR-triggered lock test, %d s. HAODR_SEL=%d ODR=0x%X NODR=%d BDR=0x%X\n",
                  SELFTEST_SECS, HAODR_SEL, TRIG_ODR_CODE, ODR_TRIG_NODR, FIFO_BDR_CODE);
    memset(&s_t2, 0, sizeof s_t2);
    s_t2.mask = init_sensors(MASK_ALL);
    sync_start(REF_HZ);
    s_t2.phase = ST_WARM;
    s_t2.t_phase = esp_timer_get_time();
    snprintf(s_tmp, sizeof s_tmp, "{\"done\":false,\"running\":true}");
    publish_selftest(s_tmp);
    return ESP_OK;
}

static void selftest_report(void)
{
    uint32_t edges = edge_count(EDGE_SYNC) - s_t2.e0;
    double mn = 1e9, mx = 0;
    uint32_t ec = edge_count(EDGE_SYNC);
    for (uint32_t k = ec > 30 ? ec - 30 : 0; k + 1 < ec; k++)
    {
        int64_t a, b;
        if (edge_time(EDGE_SYNC, k, &a) == 0 && edge_time(EDGE_SYNC, k + 1, &b) == 0)
        {
            double d = (b - a) / 1000.0;
            if (d < mn) mn = d;
            if (d > mx) mx = d;
        }
    }
    if (edges == 0) { mn = mx = 0; }
    double denom = edges ? (double)edges : 1.0;
    bool all_ok = edges > 0 && s_t2.mask != 0;

    netlog_printf("\n[SELFTEST] reference edges: %u in %d s (interval %.3f .. %.3f ms), expected %d samples/edge\n",
                  (unsigned)edges, SELFTEST_SECS, mn, mx, SAMPLES_PER_REF);
    netlog_printf(" sensor |  accel |  gyro |    ts | other | ovr | accel/edge | gyro/edge | ok\n");
    int len = snprintf(s_tmp, sizeof s_tmp,
                       "{\"done\":true,\"edges\":%u,\"secs\":%d,\"expected\":%d,\"interval_ms\":[%.3f,%.3f],\"sensors\":[",
                       (unsigned)edges, SELFTEST_SECS, SAMPLES_PER_REF, mn, mx);
    for (int i = 0; i < NUM_IMU; i++)
    {
        bool present = s_t2.mask & (1u << i);
        fifo_count_t *c = &s_t2.c[i];
        double xe = c->xl / denom, ge = c->gy / denom;
        bool ok = present && c->ovr == 0 &&
                  xe > SAMPLES_PER_REF - SELFTEST_TOL && xe < SAMPLES_PER_REF + SELFTEST_TOL &&
                  ge > SAMPLES_PER_REF - SELFTEST_TOL && ge < SAMPLES_PER_REF + SELFTEST_TOL;
        if (present && !ok) all_ok = false;
        if (present)
            netlog_printf(" %-6s | %6u | %5u | %5u | %5u | %3u | %10.2f | %9.2f | %s\n", uname(i),
                          (unsigned)c->xl, (unsigned)c->gy, (unsigned)c->ts, (unsigned)c->other,
                          (unsigned)c->ovr, xe, ge, ok ? "yes" : "NO");
        else
            netlog_printf(" %-6s | no response\n", uname(i));
        if (len < (int)sizeof s_tmp)
            len += snprintf(s_tmp + len, sizeof s_tmp - len,
                            "%s{\"id\":\"%s\",\"present\":%s,\"xl_per_edge\":%.2f,\"gy_per_edge\":%.2f,\"ovr\":%u,\"ok\":%s}",
                            i ? "," : "", uname(i), present ? "true" : "false", xe, ge,
                            (unsigned)c->ovr, ok ? "true" : "false");
    }
    if (len < (int)sizeof s_tmp)
        snprintf(s_tmp + len, sizeof s_tmp - len, "],\"ok\":%s}", all_ok ? "true" : "false");
    publish_selftest(s_tmp);
    netlog_printf("[SELFTEST] %s\n", all_ok ? "PASS: every sensor is locked to the reference"
                                            : "FAIL: note the numbers; check HAODR_SEL / ODR / BDR codes");
}

// returns the mode to switch to, or -1 to stay
static int selftest_tick(void)
{
    int64_t now = esp_timer_get_time();
    switch (s_t2.phase)
    {
    case ST_WARM:
        if (now - s_t2.t_phase < 500000) return -1;
        arm_after_edge(s_t2.mask, false);
        s_t2.phase = ST_SETTLE;
        s_t2.t_phase = esp_timer_get_time();
        return -1;
    case ST_SETTLE:
        if (now - s_t2.t_phase < 500000) return -1;
        for (int i = 0; i < NUM_IMU; i++)
            if (s_t2.mask & (1u << i)) drain_sensor(i, false, NULL, NULL);   // discard warm-up
        memset(s_t2.c, 0, sizeof s_t2.c);
        s_t2.e0 = edge_count(EDGE_SYNC);
        s_t2.t0 = esp_timer_get_time();
        s_t2.phase = ST_MEASURE;
        return -1;
    default:
        for (int i = 0; i < NUM_IMU; i++)
            if (s_t2.mask & (1u << i)) drain_sensor(i, false, NULL, &s_t2.c[i]);
        if (now - s_t2.t0 < SELFTEST_SECS * 1000000LL) return -1;
        selftest_report();
        return MODE_IDLE;
    }
}

static void selftest_exit(void)
{
    power_down_mask(s_t2.mask);
    sync_stop();
}

// ---------------------------------------------------------------- mode: run (stage 3)
static struct {
    uint16_t mask;
    uint32_t seq[NUM_IMU];
    fifo_count_t c[NUM_IMU];
    int64_t next_status, next_config;
    uint32_t ovr_total;
} s_run;

static void pop_edges(void)
{
    uint32_t index;
    int64_t t;
    while (edge_pop(EDGE_SYNC, &index, &t))
    {
        rec_hdr_t h = { .type = REC_SYNC, .seq = index, .t_us = t };
        rec_put(&h, NULL);
    }
    while (edge_pop(EDGE_DRDY, &index, &t))
    {
        rec_hdr_t h = { .type = REC_DRDY, .seq = index, .t_us = t };
        rec_put(&h, NULL);
    }
    uint8_t state;
    while (led_edge_pop(&index, &state, &t))
    {
        rec_hdr_t h = { .type = REC_LED, .sensor = state, .seq = index, .t_us = t };
        rec_put(&h, NULL);
    }
}

typedef struct { const app_cmd_t *c; uint16_t mask; const char *file; int64_t t0; } run_info_t;
static void run_begin_fn(app_status_t *s, const void *a)
{
    const run_info_t *r = a;
    s->run_mask = r->mask;
    s->run_sd = r->c->sd;
    s->run_t0_us = r->t0;
    s->run_fifo_ovr = 0;
    s->run_records = 0;
    snprintf(s->run_file, sizeof s->run_file, "%s", r->file);
    snprintf(s->run_label, sizeof s->run_label, "%s", r->c->text);
}

static esp_err_t run_enter(const app_cmd_t *c)
{
    memset(&s_run, 0, sizeof s_run);
    uint16_t want = c->mask ? c->mask : MASK_ALL;
    s_run.mask = init_sensors(want);
    if (s_run.mask == 0)
    {
        set_error("run: none of the requested sensors (0x%04X) answered", want);
        return ESP_FAIL;
    }
    if (s_run.mask != want) set_error("run: sensors 0x%04X missing, running with 0x%04X", want & ~s_run.mask, s_run.mask);

    char file[20] = "";
    if (c->sd)
    {
        esp_err_t e = sdlog_open(file, sizeof file);
        if (e != ESP_OK)
        {
            power_down_mask(s_run.mask);
            set_error("run: SD card: %s (use sd=0 for stream-only)", esp_err_to_name(e));
            return e;
        }
    }
    else if (!stream_active())
    {
        ESP_LOGW(TAG, "run without SD and without a stream listener: data goes nowhere until /stream");
    }
    run_info_t ri = { .c = c, .mask = s_run.mask, .file = file, .t0 = esp_timer_get_time() };
    st_update(run_begin_fn, &ri);

    sync_start(REF_HZ);
    if (s_run.mask & (1u << IMU_CENTRE)) drdy_arm();
    vTaskDelay(pdMS_TO_TICKS(300));

    int64_t now = esp_timer_get_time();
    emit_config(now);
    rec_note(now, "run start label=%s mask=0x%04X sd=%d file=%s", c->text, s_run.mask, c->sd, file);
    arm_after_edge(s_run.mask, true);
    led_code_start();
    s_run.next_status = 0;
    s_run.next_config = esp_timer_get_time() + CONFIG_EVERY_MS * 1000LL;
    netlog_printf("[RUN] started: sensors 0x%04X, %s%s\n", s_run.mask, c->sd ? file : "no SD",
                  stream_active() ? ", streaming" : "");
    return ESP_OK;
}

static void run_counts_fn(app_status_t *s, const void *a)
{
    s->run_fifo_ovr = s_run.ovr_total;
    s->run_records = *(const uint32_t *)a;
}

static int run_tick(void)
{
    // random 0..POLL_JITTER_MS ms: without it the sweep phase vs. the 5 ms sample grid stays
    // almost constant (same crystal), and the host can't always pin each sensor's phase.
    vTaskDelay(pdMS_TO_TICKS(esp_random() % (POLL_JITTER_MS + 1)));

    for (int i = 0; i < NUM_IMU; i++)
    {
        if (!(s_run.mask & (1u << i))) continue;
        uint32_t ovr0 = s_run.c[i].ovr;
        drain_sensor(i, true, &s_run.seq[i], &s_run.c[i]);
        s_run.ovr_total += s_run.c[i].ovr - ovr0;
    }
    pop_edges();

    int64_t now = esp_timer_get_time();
    if (now >= s_run.next_config)
    {
        emit_config(now);
        s_run.next_config = now + CONFIG_EVERY_MS * 1000LL;
    }
    if (now >= s_run.next_status)
    {
        s_run.next_status = now + 1000000;
        rec_status_t st = {
            .vbat_mv = (uint16_t)s_st.vbat_mv, .vbus_mv = (uint16_t)s_st.vbus_mv,
            .sd_drops = sdlog_dropped(), .stream_drops = stream_dropped(),
            .rssi_dbm = (int8_t)wifi_rssi(), .mode = MODE_RUN,
            .fifo_ovr = (uint16_t)(s_run.ovr_total > 0xFFFF ? 0xFFFF : s_run.ovr_total),
        };
        rec_hdr_t h = { .type = REC_STATUS, .nbytes = sizeof st, .t_us = now };
        rec_put(&h, &st);
        uint32_t recs = 0;
        for (int i = 0; i < NUM_IMU; i++) recs += s_run.seq[i];
        st_update(run_counts_fn, &recs);
        ESP_LOGI(TAG, "run t=%llds vbat=%dmV sd_drops=%u stream_drops=%u fifo_ovr=%u edges=%u",
                 (long long)((now - s_st.run_t0_us) / 1000000), s_st.vbat_mv, (unsigned)sdlog_dropped(),
                 (unsigned)stream_dropped(), (unsigned)s_run.ovr_total, (unsigned)edge_count(EDGE_SYNC));
        if (sdlog_write_errors()) set_error("SD write errors: %u", (unsigned)sdlog_write_errors());
    }
    return -1;
}

static void run_exit(void)
{
    led_code_stop();
    for (int i = 0; i < NUM_IMU; i++)             // last <= 100 ms still in the FIFOs
        if (s_run.mask & (1u << i)) drain_sensor(i, true, &s_run.seq[i], &s_run.c[i]);
    power_down_mask(s_run.mask);
    if (s_run.mask & (1u << IMU_CENTRE)) drdy_disarm();
    sync_stop();
    pop_edges();
    int64_t now = esp_timer_get_time();
    rec_note(now, "run end sd_drops=%u stream_drops=%u fifo_ovr=%u", (unsigned)sdlog_dropped(),
             (unsigned)stream_dropped(), (unsigned)s_run.ovr_total);
    sdlog_close();
    netlog_printf("[RUN] stopped after %lld s. Log closed, safe to remove the SD card.\n",
                  (long long)((now - s_st.run_t0_us) / 1000000));
}

// ---------------------------------------------------------------- mode switching department
static void hz_fn(app_status_t *s, const void *a) { s->flicker_hz = *(const int *)a; }

static void mode_exit(app_mode_t m)
{
    switch (m)
    {
    case MODE_CHECK:    check_exit(); break;
    case MODE_SELFTEST: selftest_exit(); break;
    case MODE_RUN:      run_exit(); break;
    case MODE_FLICKER:  led_flicker_stop(); break;
    default: break;
    }
}

static esp_err_t mode_enter(const app_cmd_t *c)
{
    switch (c->mode)
    {
    case MODE_CHECK:    return check_enter();
    case MODE_SELFTEST: return selftest_enter();
    case MODE_RUN:      return run_enter(c);
    case MODE_FLICKER:
    {
        int hz = c->hz > 0 ? c->hz : FLICKER_HZ_DEFAULT;
        led_flicker_start(hz);
        st_update(hz_fn, &hz);
        netlog_printf("[FLICKER] sync LEDs at %d Hz, 50 %%\n", hz);
        return ESP_OK;
    }
    default:            return ESP_OK;
    }
}

static app_mode_t s_cur = MODE_IDLE;

static void switch_to(const app_cmd_t *c)
{
    mode_exit(s_cur);
    s_cur = MODE_IDLE;
    st_update(set_mode_fn, &s_cur);
    if (c->mode != MODE_IDLE && mode_enter(c) == ESP_OK) s_cur = c->mode;
    st_update(set_mode_fn, &s_cur);
    ESP_LOGI(TAG, "mode -> %s", app_mode_name(s_cur));
}

static void switch_simple(app_mode_t m)
{
    app_cmd_t c = { .kind = CMD_MODE, .mode = m, .sd = true };
    switch_to(&c);
}

static void handle(const app_cmd_t *c)
{
    switch (c->kind)
    {
    case CMD_MODE:  switch_to(c); break;
    case CMD_STOP:  switch_simple(MODE_IDLE); break;
    case CMD_MARK:
        if (s_cur == MODE_RUN) rec_note(esp_timer_get_time(), "mark %s", c->text);
        break;
    case CMD_BLINK_MODE:
        s_blink_n = s_cur;
        s_blink_ticks = 0;
        break;
    }
}

static void vbat_fn(app_status_t *s, const void *a)
{
    const int *v = a;
    s->vbat_mv = v[0];
    s->vbus_mv = v[1];
}

static void controller(void *arg)
{
    app_cmd_t boot = { .kind = CMD_MODE, .mode = (app_mode_t)(uintptr_t)arg, .sd = true };
    if (boot.mode != MODE_IDLE) switch_to(&boot);

    TickType_t last = xTaskGetTickCount();
    int64_t next_power = 0;
    for (;;)
    {
        app_cmd_t c;
        while (xQueueReceive(s_q, &c, 0) == pdTRUE) handle(&c);

        int64_t now = esp_timer_get_time();
        if (now >= next_power)
        {
            next_power = now + 1000000;
            int v[2] = { power_vbat_mv(), power_vbus_mv() };
            st_update(vbat_fn, v);
            if (v[0] > 2500 && v[0] < LOW_BATT_MV && v[1] < 4000 && s_cur == MODE_RUN)
            {
                set_error("low battery (%d mV): run stopped", v[0]);
                switch_simple(MODE_IDLE);
            }
        }

        int next = -1;
        switch (s_cur)
        {
        case MODE_CHECK:    check_tick(); break;
        case MODE_SELFTEST: next = selftest_tick(); break;
        case MODE_RUN:      next = run_tick(); break;
        default: break;
        }
        if (next >= 0) switch_simple((app_mode_t)next);
        status_led(s_cur, esp_timer_get_time());

        if (xTaskDelayUntil(&last, pdMS_TO_TICKS(POLL_MS)) == pdFALSE) last = xTaskGetTickCount();
    }
}

esp_err_t app_start(app_mode_t boot_mode)
{
    gpio_reset_pin(PIN_STATUS);
    gpio_set_direction(PIN_STATUS, GPIO_MODE_OUTPUT);
    power_init();
    // The IMUs are not power-cycled by an ESP reset (OTA, crash): reset + power down all of
    // them so nothing keeps running from the previous boot, and learn which ones answer.
    uint16_t ok = init_sensors(MASK_ALL);
    power_down_mask(ok);
    s_st.ok_mask = ok;
    ESP_LOGI(TAG, "%d / %d sensors answered (mask 0x%04X)", __builtin_popcount(ok), NUM_IMU, ok);

    s_q = xQueueCreate(8, sizeof(app_cmd_t));
    if (!s_q) return ESP_ERR_NO_MEM;
    // Core 1: SPI polling and SD/Wi-Fi work (core 0) don't get in each other's way.
    BaseType_t r = xTaskCreatePinnedToCore(controller, "ctl", 8192,
                                           (void *)(uintptr_t)(boot_mode < MODE_COUNT ? boot_mode : MODE_IDLE),
                                           5, NULL, 1);
    return r == pdPASS ? ESP_OK : ESP_ERR_NO_MEM;
}

// ---------------------------------------------------------------- JSON department
int app_status_json(char *buf, size_t n)
{
    app_status_t s;
    taskENTER_CRITICAL(&s_mux);
    s = s_st;
    taskEXIT_CRITICAL(&s_mux);
    const esp_app_desc_t *app = esp_app_get_description();
    const esp_partition_t *part = esp_ota_get_running_partition();
    int64_t now = esp_timer_get_time();
    return snprintf(buf, n,
        "{\"fw\":\"%s\",\"built\":\"%s %s\",\"partition\":\"%s\","
        "\"mode\":%u,\"mode_name\":\"%s\",\"boot_mode\":%u,\"running\":%s,"
        "\"sensors_ok\":\"0x%04X\",\"vbat_mv\":%d,\"vbus_mv\":%d,"
        "\"rssi_dbm\":%d,\"uptime_s\":%lld,\"free_heap\":%lu,\"esp_time_us\":%lld,"
        "\"stream\":{\"active\":%s,\"packets\":%u,\"drops\":%u,\"ring\":%u},\"sd_ring\":%u,"
        "\"run\":{\"file\":\"%s\",\"label\":\"%s\",\"mask\":\"0x%04X\",\"sd\":%s,\"t_s\":%lld,"
        "\"records\":%u,\"sd_drops\":%u,\"sd_write_errors\":%u,\"fifo_ovr\":%u},"
        "\"flicker_hz\":%d,\"last_error\":\"%s\"}",
        app->version, app->date, app->time, part ? part->label : "?",
        (unsigned)s.mode, app_mode_name(s.mode), (unsigned)settings_boot_mode(),
        s.mode == MODE_RUN ? "true" : "false",
        s.ok_mask, s.vbat_mv, s.vbus_mv, wifi_rssi(), (long long)(now / 1000000),
        (unsigned long)esp_get_free_heap_size(), (long long)now,
        stream_active() ? "true" : "false", (unsigned)stream_packets(), (unsigned)stream_dropped(),
        (unsigned)stream_ring_bytes(), (unsigned)sdlog_ring_bytes(),
        s.run_file, s.run_label, s.run_mask, s.run_sd ? "true" : "false",
        s.mode == MODE_RUN ? (long long)((now - s.run_t0_us) / 1000000) : 0LL,
        (unsigned)s.run_records, (unsigned)sdlog_dropped(), (unsigned)sdlog_write_errors(),
        (unsigned)s.run_fifo_ovr, s.flicker_hz, s.last_error);
}

int app_selftest_json(char *buf, size_t n)
{
    if (n == 0) return 0;
    size_t m = n < sizeof s_selftest ? n : sizeof s_selftest;
    taskENTER_CRITICAL(&s_mux);
    memcpy(buf, s_selftest, m);
    taskEXIT_CRITICAL(&s_mux);
    buf[m - 1] = '\0';
    return (int)strlen(buf);
}
