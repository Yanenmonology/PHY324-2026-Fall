// Coded pulse train on the sync LEDs. The intervals are pseudo-random (30..250 ms) so that
// the camera-side matcher in the vision pipeline has a unique pattern to lock on to.
// Each edge's time is taken with esp_timer (systimer, clocked from the 40 MHz crystal),
// i.e. the same time base as the IMU_SYNC edge records, and logged as REC_LED.
#include "led.h"
#include "pins.h"
#include "config.h"
#include "driver/gpio.h"
#include "driver/ledc.h"
#include "esp_timer.h"
#include "esp_random.h"
#include "esp_err.h"
#include "freertos/FreeRTOS.h"

#define HIST 64          // edges are >= 30 ms apart; main loop drains every 100 ms

static esp_timer_handle_t s_timer;
static portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;
static volatile int64_t s_t[HIST];
static volatile uint8_t s_state_hist[HIST];
static volatile uint32_t s_n;
static uint32_t s_popped;
static uint8_t  s_state;
static uint32_t s_rng;

static uint32_t xorshift32(void)
{
    uint32_t x = s_rng;
    x ^= x << 13; x ^= x >> 17; x ^= x << 5;
    return s_rng = x;
}

static void set_led(bool on) { gpio_set_level(PIN_LED_GATE, on ? 0 : 1); }   // active low

static void on_timer(void *arg)
{
    (void)arg;
    s_state ^= 1;
    taskENTER_CRITICAL(&s_mux); // keep the timestamp and the pin change together
    set_led(s_state);
    int64_t t = esp_timer_get_time();
    taskEXIT_CRITICAL(&s_mux);
    s_t[s_n & (HIST - 1)] = t;
    s_state_hist[s_n & (HIST - 1)] = s_state;
    s_n++;
    uint32_t ms = LED_MIN_MS + xorshift32() % (LED_MAX_MS - LED_MIN_MS + 1);
    esp_timer_start_once(s_timer, (uint64_t)ms * 1000);
}

void led_init(void)
{
    gpio_reset_pin(PIN_LED_GATE);
    gpio_set_level(PIN_LED_GATE, 1);
    gpio_set_direction(PIN_LED_GATE, GPIO_MODE_OUTPUT);
    gpio_set_level(PIN_LED_GATE, 1);
}

void led_code_start(void)
{
    if (!s_timer)
    {
        const esp_timer_create_args_t a = { .callback = on_timer, .name = "syncled" };
        ESP_ERROR_CHECK(esp_timer_create(&a, &s_timer));
    }
    s_rng = esp_random() | 1;
    s_n = 0;
    s_popped = 0;
    s_state = 0;
    set_led(false);
    esp_timer_start_once(s_timer, 500 * 1000);// first edge 0.5 s after start
}

void led_code_stop(void)
{
    if (s_timer) esp_timer_stop(s_timer);
    set_led(false);
}

int led_edge_pop(uint32_t *index, uint8_t *state, int64_t *t_us)
{
    if (s_popped >= s_n) return 0;
    if (s_n - s_popped > HIST) s_popped = s_n - HIST;
    *index = s_popped;
    *state = s_state_hist[s_popped & (HIST - 1)];
    *t_us  = s_t[s_popped & (HIST - 1)];
    s_popped++;
    return 1;
}
// Flicker: LEDC low-speed timer 1 / channel 1, XTAL clock (must match sync.c: on the S3 all
// low-speed timers share one clock source). 50 % duty, so the active-low gate doesn't matter.
void led_flicker_start(int hz)
{
    if (hz < 1) hz = FLICKER_HZ_DEFAULT;
    led_code_stop();
    int res = 14;
    while (res > 1 && 40000000LL / ((int64_t)hz << res) < 1) res--;   // divider must be >= 1
    ledc_timer_config_t tc = {
        .speed_mode = LEDC_LOW_SPEED_MODE, .duty_resolution = (ledc_timer_bit_t)res,
        .timer_num = LEDC_TIMER_1, .freq_hz = hz, .clk_cfg = LEDC_USE_XTAL_CLK,
    };
    ESP_ERROR_CHECK(ledc_timer_config(&tc));
    ledc_channel_config_t cc = {
        .gpio_num = PIN_LED_GATE, .speed_mode = LEDC_LOW_SPEED_MODE, .channel = LEDC_CHANNEL_1,
        .timer_sel = LEDC_TIMER_1, .duty = 1u << (res - 1), .hpoint = 0,
    };
    ESP_ERROR_CHECK(ledc_channel_config(&cc));
}

void led_flicker_stop(void)
{
    ledc_stop(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_1, 1);   // idle high = off
    led_init();   // gpio_set_direction() re-routes the pad from LEDC back to plain GPIO
}
