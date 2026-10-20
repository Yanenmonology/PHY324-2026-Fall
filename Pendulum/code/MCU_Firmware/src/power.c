#include "power.h"
#include "pins.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"
#include "esp_err.h"

static adc_oneshot_unit_handle_t s_adc;
static adc_cali_handle_t s_cali;

void power_init(void)
{
    if (s_adc) return;
    adc_oneshot_unit_init_cfg_t a = { .unit_id = ADC_UNIT_1 };
    ESP_ERROR_CHECK(adc_oneshot_new_unit(&a, &s_adc));
    adc_oneshot_chan_cfg_t b = { .atten = ADC_ATTEN_DB_12, .bitwidth = ADC_BITWIDTH_DEFAULT };
    ESP_ERROR_CHECK(adc_oneshot_config_channel(s_adc, ADC_CH_VBAT, &b));
    ESP_ERROR_CHECK(adc_oneshot_config_channel(s_adc, ADC_CH_VBUS, &b));
    adc_cali_curve_fitting_config_t c = {
        .unit_id = ADC_UNIT_1, .atten = ADC_ATTEN_DB_12, .bitwidth = ADC_BITWIDTH_DEFAULT
    };
    if (adc_cali_create_scheme_curve_fitting(&c, &s_cali) != ESP_OK) s_cali = NULL;
}

static int read_mv(adc_channel_t ch)
{
    int raw = 0, mv = 0;
    long acc = 0;
    for (int i = 0; i < 8; i++)
    {
        adc_oneshot_read(s_adc, ch, &raw);
        acc += raw;
    }
    raw = acc / 8;
    if (s_cali) adc_cali_raw_to_voltage(s_cali, raw, &mv);
    else mv = raw * 3100 / 4095;
    return mv * 2;   // divider /2 on the board
}

int power_vbat_mv(void) 
{ 
    return read_mv(ADC_CH_VBAT); 
}

int power_vbus_mv(void) 
{ 
    return read_mv(ADC_CH_VBUS); 
}