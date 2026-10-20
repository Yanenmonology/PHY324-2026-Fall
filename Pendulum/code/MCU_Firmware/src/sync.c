#include "sync.h"
#include "pins.h"
#include "driver/ledc.h"
#include "driver/gpio.h"
#include "hal/gpio_ll.h"
#include "esp_timer.h"
#include "esp_attr.h"
#include "esp_err.h"

#define HIST 512
typedef struct
{
    volatile int64_t t[HIST];
    volatile uint32_t n;       // edges seen
    uint32_t popped;
}edge_ring_t;

static edge_ring_t s_ring[EDGE_NCH];

static void IRAM_ATTR edge_isr(void *arg)
{
    int64_t t = esp_timer_get_time();
    edge_ring_t *r = (edge_ring_t *)arg;
    r->t[r->n & (HIST - 1)] = t;
    r->n++;
}

static void ring_reset(edge_ring_t *r)
{
    r->n = 0;
    r->popped = 0;
}

void sync_init(void)
{
    gpio_install_isr_service(ESP_INTR_FLAG_IRAM);
}

void sync_start(int hz)
{
    ring_reset(&s_ring[EDGE_SYNC]);
    // set led to 10Hz
    ledc_timer_config_t tc = {
        .speed_mode = LEDC_LOW_SPEED_MODE, .duty_resolution = LEDC_TIMER_14_BIT,
        .timer_num = LEDC_TIMER_0, .freq_hz = hz, .clk_cfg = LEDC_USE_XTAL_CLK,
    };
    ESP_ERROR_CHECK(ledc_timer_config(&tc));
    ledc_channel_config_t cc = {
        .gpio_num = PIN_IMU_SYNC, .speed_mode = LEDC_LOW_SPEED_MODE, .channel = LEDC_CHANNEL_0,
        .timer_sel = LEDC_TIMER_0, .duty = 1 << 13, .hpoint = 0,   // 50 %
    };
    ESP_ERROR_CHECK(ledc_channel_config(&cc));
    gpio_ll_input_enable(&GPIO, PIN_IMU_SYNC);// read our own output back for steaming and syncing
    gpio_set_intr_type(PIN_IMU_SYNC, GPIO_INTR_POSEDGE);
    gpio_isr_handler_add(PIN_IMU_SYNC, edge_isr, &s_ring[EDGE_SYNC]);
}

void sync_stop(void)
{
    gpio_isr_handler_remove(PIN_IMU_SYNC);
    ledc_stop(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0, 0);
}

// Interruption control:
void drdy_arm(void)
{
    ring_reset(&s_ring[EDGE_DRDY]);
    gpio_config_t io = {
        .pin_bit_mask = 1ULL << PIN_IMU_INT,
        .mode = GPIO_MODE_INPUT,
        .pull_down_en = GPIO_PULLDOWN_ENABLE,
        .intr_type = GPIO_INTR_POSEDGE,
    };
    gpio_config(&io);
    gpio_isr_handler_add(PIN_IMU_INT, edge_isr, &s_ring[EDGE_DRDY]);
}

void drdy_disarm(void)
{
    gpio_isr_handler_remove(PIN_IMU_INT);
    gpio_set_intr_type(PIN_IMU_INT, GPIO_INTR_DISABLE);
}

uint32_t edge_count(edge_ch_t ch)
{
    return s_ring[ch].n; 
}

int edge_time(edge_ch_t ch, uint32_t index, int64_t *t_us)
{
    edge_ring_t *r = &s_ring[ch];
    uint32_t n = r->n;
    if (index >= n || n - index > HIST) return -1;
    *t_us = r->t[index & (HIST - 1)];
    return 0;
}

int edge_pop(edge_ch_t ch, uint32_t *index, int64_t *t_us)
{
    edge_ring_t *r = &s_ring[ch];
    uint32_t n = r->n;
    if (r->popped >= n) return 0;
    if (n - r->popped > HIST) r->popped = n - HIST; // overrun: skip to the oldest kept
    *index = r->popped;
    *t_us = r->t[r->popped & (HIST - 1)];
    r->popped++;
    return 1;
}