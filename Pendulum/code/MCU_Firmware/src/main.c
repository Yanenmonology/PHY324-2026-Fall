#include <stdio.h>
#include <string.h>
#include <math.h>
#include "config.h"
#include "pins.h"
#include "imu.h"
#include "sync.h"
#include "sdlog.h"
#include "esp_server.h"
#include "netlog.h"
#include "wifi.h"

#include "driver/gpio.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_app_desc.h"

static const char *TAG = "weight";
static bool s_ok[NUM_IMU];
static const char *name(int i){
    static char b[8];
    snprintf(b, sizeof b, "U%d", i + 1);
    return b;
}
static adc_oneshot_unit_handle_t s_adc;
static adc_cali_handle_t s_cali;

static void adc_setup(void)
{
    adc_oneshot_unit_init_cfg_t a ={ .unit_id = ADC_UNIT_1 };
    ESP_ERROR_CHECK(adc_oneshot_new_unit(&a, &s_adc));
    adc_oneshot_chan_cfg_t b = { .atten = ADC_ATTEN_DB_12, .bitwidth = ADC_BITWIDTH_DEFAULT};
    ESP_ERROR_CHECK(adc_oneshot_config_channel(s_adc, ADC_CH_VBAT, &b));
    ESP_ERROR_CHECK(adc_oneshot_config_channel(s_adc, ADC_CH_VBUS, &b));
    adc_cali_curve_fitting_config_t c = { 
        .unit_id = ADC_UNIT_1, .atten = ADC_ATTEN_DB_12, .bitwidth = ADC_BIDWIDTH_DEFAULT
    };
    if(adc_cali_create_scheme_curve_fitting(&c, &s_cali) != ESP_OK)
    {
        s_cali = NULL;
    }
}

static int read_mv(adc_channel_t ch)
{
    int raw = 0;
    int mv = 0;
    long acc = 0;
    for(int i = 0; i < 8; i++)
    {
        adc_oneshot_read(s_adc, ch, &raw);
        acc += raw;
    }
    raw = acc / 8
    if(s_cali)
    {
        adc_cali_raw_to_voltage(s_cali, raw, &mv);
    }
    else
    {
        mv = raw * 3100 / 4095;
    }
    return mv * 2;
}

static void inittial_stage(void)
{
    ESP_LOGI(TAG, "[STAGE 1]: id check and free-run reading, board need to be face up.");
    int good = 0;
    for(int i = 0; i < NUM_IMU; i++)
    {
        s_ok[i] = imu_init(i);
        good += s_ok[i];
    }
    ESP_LOGI(TAG, "[STAGE 1]: %d / %d sensor answered.", good, NUM_IMU);
    vTaskDelay(pdMS_TO_TICKS(300));
    while(1)
    {
        printf("\n sensor |     ax(g)     ay(g)     az(g) |     gx(dps)     gy(dps)     gz(dps)\n");
        for(int i = 0; i < NUM_IMU; i++)
        {
            if(!s_ok[i])
            {
                printf(" %-5s | no response\n", name(i));
                continue;
            }
            uint8_t b[12];
            imu_read(i, R_OUTX_L_G, b,12);
            printf(" %-5s |   %7.3f   %7.3f   %7.3f |   %8.2f   %8.2f   %8.2f\n", name(i),
                le16(b+6) * XL_G_PER_LSB, le16(b+8) * XL_G_PER_LSB, le16(b+10) * XL_G_PER_LSB,
                le16(b) * G_DPS_PER_LSB, le16(b+2) * G_DPS_PER_LSB, le16(b+4) * G_DPS_PER_LSB
            );
        }
        vTaskDelay(dpMS_TO_TICKS(1000));
    }
}

static int s_xl[NUM_IMU];
static int s_gy[NUM_IMU];
static int s_ts[NUM_IMU];
static int s_other[NUM_IMU];

static void drain_count(int i)
{
    static uint8_t buf[140 * FIFO_WORD_BYTES];
    int lvl = imu_fifo_status(i);
    while (lvl > 0)
    {
        int n = lvl > 140 ? 140 : lvl;
        imu_fifo_read(i, buf, n);
        for(int m = 0; m < n; m++)
        {
            int tag = buf[m*7] >> 3;
            if(tag == TAG_XL_NC)
            {
                s_xl[i]++;
            }
            else if(tag == TAG_GY_NC)
            {
                s_gy[i]++;
            }
            else if (tag == TAG_TIMESTAMP)
            {
                s_ts[i]++;
            }
            else{
                s_other[i]++;
            }
        }
        lvl -= n;
    }   
}

static void start_all_triggered(void)
{
    for(int i = 0; i < NUM_IMU; i++)
    {
        if(s_ok[i]) imu_start_triggered(i);
    }
}

static void stage2(void)
{
    ESP_LOGI(TAG,"\n[STAGE 2]: ODR trigger self test. \n [s2-stats]HAODR _SEL = %d, ODR code: 0x%X, NODR = %d, BDR: 0x%X",
            HAODR_SEL, TRIG_ODR_CODE, ODR_TRIG_NODR, FIFO_BDR_CODE);
    for(int i = 0, i < NUM_IMU; i++)
    {
        s_ok[i] = imu_init(i);
    }
    sync_start(REF_HZ);
    vTaskDelay(pdMS_TO_TICKS(500));
    start_all_triggered();
    const int secs = 10;
    int64_t t0 = esp_timer_get_time();
    uint32_t e0 = sync_edge_count();
    wile(esp_timer_get_time() - t0 < secs * 1000000LL)
    {
        for(int i = 0; i < NUM_IMU; i++)
        {
            if(s_ok[i]) drain_count(i);
            vTaskDelay(pdMS_TO_TICKS(POLL_MS));
        }
    }
    uint32_t edges = sync_edge_count() - e0;
    double mn = 1e9;
    double mx = 0
    for (uint32_t k = sync_edge_count() - 30; k < sync_edge_count() - 1; k++)
    {
        int64_t a;
        int64_t b;
        if(sync_edge_time(k, &a) == 0 && sync_edge_time(k+1, &b) == 0)
        {
            double d = (b - a) / 1000.0;
            if(d < mn) mn = d;
            if(d > mx) mx = d;
        }
    }
    int expected = 2 * ODR_TRIG_NODR;
    printf("\n[STAGE 2]Reference edges: %u in %d s (interval %.3f ..%.3f ms)\n",(unsigned)edges, secs, mn, mx);
    printf("[STAGE 2]Expected acceleration samples per edge: %d\n", expected);
    printf(" Sensor   |  accel  |  gyro  |    ts   |   other  | accel/edge | gyro/edge \n");
    for(int i=0; i < NUM_IMU; i++)
    {
        if(!s_ok[i]){printf("[S2-ERROR]  %-5s | No response\n", name(i)); continue;}
        printf("  %-5s | %5d | %4d | %2d | %5d | %9.2f | %8.2f\n", name(i), s_xl[i], s_gy[i], s_ts[i],
               s_other[i], (double)s_xl[i] / edges, (double)s_gy[i] / edges);
    }
    printf("[NOTE] if all accel/edge and gyro/edge are the same on all sensors and equal to expected value.\n 
        Other wise note down the number and change HAODR_SEL, ODR, BDR codes.");
    while (1)
    {
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}

static void stage3(void)
{
    ESP_LOGI(TAG, "[STAGE 3] logging department. Press boot to stop and close file.")
    adc_setup();
    gpio_set_direction(0, GPIO_MODE_INPUT);
    gpio_set_direction(PIN_STATUS, GPIO_MODE_OUTPUT);
    for (int i = 0; i < NUM_IMU; i++){s_ok[i]=imu_init(i);}
    if(sdlog_open() != ESP_OK)
    {
        ESP_LOGE(TAG, "[S3- ERROR] No sd card, cannot log");
        while(1)
        {
            gpio_set_level(PIN_STATUS, 1);
            vTaskDelay(1000);
            gpio_set_level(PIN_STATUS, 0);
            vTaskDelay(1000);
        }
    }
    // logging
    sync_start(REF_HZ);
    vTaskDelay(pdMS_TO_TICKS(500));
    start_all_triggered();
    rec_hdr_t note = { .type = REC_NOTE, .t_us = esp_timer_get_time()};
    char text[96];
    note.nbytes = snprintf(text, sizeof text, "ref=%dHZ,nodr=%d, odr=0x%X, bdr=0x%X, fs_xl=%d, fs_g=%d",
                            REF_HZ, ODR_TRIG_NODR, HAODR_SEL, TRIG_ODR_CODE, FIFO_BDR_CODE, FS_XL_CODE, FS_G_CODE);
    sdlog_put(&note, text);

    static uint8_t buf[140 * FIFO_WORD_BYTES];
    uint32_t seq[NUM_IMU] = {0};
    int64_t next_status = 0;
    int64_t last_place = esp_timer_get_time();
    while(gpio_get_level(0) != 0)
    {
        for(int i = 0; i < NUM_IMU; i++)
        {
            if(!s_ok[i]) continue;
            int lvl = imu_fifo_status(i);
            while (lvl > 0)
            {
                int n = lvl > 140 ? 140 : lvl;
                int64_t t = esp_timer_get_time();
                if(imu_fifo_read(i, buf, n) != ESP_OK) break;
                rec_hdr_t h = {
                    .type = REC_FIFO, .sensor = i, .nbytes = n * FIFO_WORD_BYTES, .seq = seq[i]++, .t_us = t
                };
                sdlog_put(&h, buf);
                lvl -= n;
            }
            
        }
        uint32_t index;
        uint64_t te;
        while (sync_edge_pop(&index, &te))
        {
            rec_hdr_t h = {.type = REC_SYNC, .seq = index, .t_us = te};
            sdlog_put(&h, NULL);
        }
        int64_t now = esp_timer_get_time();
        if(now >= next_status)
        {
            uint16_t st[2] = {read_mv(ADC_CH_VBAT), read_mv(ADC_CH_VBUS)};
            rec_hdr_t h = {.type = REC_STATUS, .nbytes = sizeof st, .t_us = now};
            sdlog_put(&h, st);
            ESP_LOGI(TAG, "t=%llds, vbat=%dmv, drops=%u , edges=%u", now/1000000, st[0], 
                (unsigned)sdlog_dropped, (unsigned)sync_edge_count());
            gpio_set_level(PIN_STATUS, (now/1000000)&1);
            if(st[0] > 2500 && st[0] < LOW_BATT_MV && st[1] < 4000)
            {
                ESP_LOGW(TAG, "[WARNING] low battery, stopping");
                break;
            }
            next_status = now + 1000000;
        }
        int64_t spent = esp_timer_get_time() - last_place;
        if (spent < POLL_MS * 1000)
        {
            vTaskDelay(pdMS_TO_TICKS((POLL_MS * 1000 - spent) / 1000));
            last_place = esp_timer_get_time();
        }
    }
    for (int i = 0; i < NUM_IMU; i++)
    {
        if(s_ok[i]){imu_power_down(i);}
    }
    sync_stop();
    sdlog_close();
    ESP_LOGI(TAG,"Log closed, safe to remove sd card now.");
    gpio_set_level(PIN_STATUS, 1);
    while (1)
    {
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}

void Sensor_app_main(void)
{
    imu_cs_init();
    ESP_ERROR_CHECK(imu_bus_init());
    netlog_init();

    bool wifi_ok = wifi_connect_blocking(15000);
    static const cmd_hooks_t hooks = { .is_logging = is_logging, .stop_run = stop_run };
    bool srv_ok = cmd_server_start(&hooks) == ESP_OK;
    ota_confirm_boot(wifi_ok && srv_ok);
    stage_button_start(PIN_STATUS, is_logging);
    ESP_ERROR_CHECK(imu_bus_init());
    ESP_LOGI(TAG, "fw %s, running stage %u (%s)", esp_app_get_description()->version,
             (unsigned)stage_current(), stage_name(stage_current()));

    switch (stage_current())
    {
        case 1:  inittial_stage(); break;
        case 2:  stage2();         break;
        default: stage3();         break;
    }
}