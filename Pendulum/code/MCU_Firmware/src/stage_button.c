#include "stage_button.h"
#include "stage.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "button";
#define POLL_MS        20
#define DEBOUNCE_POLLS 3
#define LONG_PRESS_MS  1500

static int    s_led = -1;
static bool (*s_is_logging)(void);

static void led(bool on)
{
    gpio_set_level(STATUS_LED_GPIO, on ? STATUS_LED_ON_LEVEL : !STATUS_LED_ON_LEVEL);
}

static void blink_count(unsigned n)
{
    led(false);
    vTaskDelay(pdMS_TO_TICKS(400));
    for(unsigned i = 0; i < n; i++)
    {
        led(true);
        vTaskDelay(pdMS_TO_TICKS(200));
        led(false);
        vTaskDelay(pdMS_TO_TICKS(200));
    }
}

static void button_task(void *arg)
{
    (void)arg;
    int stable_low = 0;
    bool pressed = false;
    bool ignore = false;   // press began during logging
    TickType_t t_down = 0;

    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(POLL_MS));
        bool low = gpio_get_level(BOOT_BUTTON_GPIO) == 0;   // board has a 10 k pull-up

        if (low) {
            if (!pressed && ++stable_low >= DEBOUNCE_POLLS) {
                pressed = true;
                ignore  = s_is_logging && s_is_logging();
                t_down  = xTaskGetTickCount();
            }
            if (pressed && !ignore &&
                (xTaskGetTickCount() - t_down) >= pdMS_TO_TICKS(LONG_PRESS_MS)) {
                uint8_t next = stage_current() % STAGE_MAX + 1;
                ESP_LOGI(TAG, "long press: switching to stage %u", (unsigned)next);
                blink_count(next);
                stage_select_and_reboot(next);
                ignore = true;          // only reached if the NVS write failed
            }
        } else {
            if (pressed && !ignore) blink_count(stage_current());   // short press
            pressed    = false;
            stable_low = 0;
        }
    }
}

esp_err_t stage_button_start(int led_gpio, bool (*is_logging)(void))
{
    s_led = led_gpio;
    s_is_logging = is_logging;

    gpio_config_t in = 
    {
        .pin_bit_mask = 1ULL << BOOT_BUTTON_GPIO,
        .mode         = GPIO_MODE_INPUT,
        .pull_up_en   = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type    = GPIO_INTR_DISABLE,
    };
    esp_err_t err = gpio_config(&in);
    if (err != ESP_OK) return err;

    gpio_config_t out = 
    {
        .pin_bit_mask = 1ULL << led_gpio,
        .mode         = GPIO_MODE_OUTPUT,
        .pull_up_en   = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type    = GPIO_INTR_DISABLE,
    };
    err = gpio_config(&out);
    if (err != ESP_OK) return err;

    BaseType_t ok = xTaskCreate(button_task, "stage_btn", 3072, NULL, 3, NULL);
    return ok == pdPASS ? ESP_OK : ESP_ERR_NO_MEM;
}