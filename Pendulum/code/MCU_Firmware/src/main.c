#include <stdio.h>
#include <string.h>
#include <math.h>
#include "config.h"
#include "pins.h"
#include "imu.h"
#include "sync.h"
#include "sdlog.h"

#include "driver/gpio.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

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
}

static int read_mv(adc_channel_t ch)
{}