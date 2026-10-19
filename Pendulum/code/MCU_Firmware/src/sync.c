#include "sync.h"
#include "pins.h"
#include "driver/ledc.h"
#include "driver/gpio.h"
#include "hal/gpio_ll.h"
#include "esp_timer.h"
#include "esp_attr.h"
#include "esp_err.h"

#define HIST 256
static volatile int64_t s_t[HIST];
static volatile uint32_t s_n; //edges passed
static uint32_t s_popped;

static void IRAM_ATTR edge_isr(void *arg)
{
    int64_t t = esp_timer_get_time();
    s_t[s_n & (HIST - 1)] = t;
    s_n++;
}

void sync_start(int hz)
{
    s_n = 0;
    s_popped = 0;
    ledc_timer_config_t tc = {
        .speed_mode = LEDC_LOW_SPEED_MODE, .duty_resolution = LEDC_TIMER_14_BIT,
        .timer_num = LEDC_TIMER_0, .freq_hz = hz, .clk_cfg = LEDC_AUTO_CLK,
    };
    ESP_ERROR_CHECK(ledc_timer_config(&tc));
    ledc_channel_config_t cc = {
        .gpio_num = PIN_IMU_SYNC, .speed_mode = LEDC_LOW_SPEED_MODE, .channel = LEDC_CHANNEL_0,
        .timer_sel = LEDC_TIMER_0, .duty = 1 << 13, .hpoint = 0,   // 50%
    };
    ESP_ERROR_CHECK(ledc_channel_config(&cc));
    gpio_ll_input_enable(&GPIO, PIN_IMU_SYNC);
    gpio_set_intr_type(PIN_IMU_SYNC, GPIO_INTR_POSEDGE);
    gpio_install_isr_service(ESP_INTR_FLAG_IRAM);
    gpio_isr_handler_add(PIN_IMU_SYNC, edge_isr, NULL);
}

void sync_stop(void)
{
    gpio_isr_handler_remove(PIN_IMU_SYNC);
    ledc_stop(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0, 0);
}

uint32_t sync_edge_count(void)
{
    return s_n;
}

int sync_edge_time(uint32_t index, int64_t *t_us)
{
    if(index >= s_n || s_n - index > HIST)
    {
        return -1;
    }
    *t_us = s_t[index & (HIST - 1)];
    return 0;
}

int sync_edge_pop(uint32_t *index, uint64_t *t_us)
{
    if(s_popped >= s_n)
    {
        return 0;
    }
    if(s_n - s_popped > HIST)
    {
        s_popped = s_n - HIST;
    }

    *index = s_popped;
    *t_us = s_t[s_popped & (HIST - 1)];
    s_popped++;
    return 1;
}