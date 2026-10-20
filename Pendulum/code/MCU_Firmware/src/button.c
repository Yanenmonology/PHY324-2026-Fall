#include "button.h"
#include "app.h"
#include "pins.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "button";
#define POLL_MS        20
#define DEBOUNCE_POLLS 3
#define LONG_PRESS_MS  1500

static void button_task(void *arg)
{
    (void)arg;
    int stable_low = 0;
    bool pressed = false, long_done = false, was_running = false;
    TickType_t t_down = 0;

    for (;;)
    {
        vTaskDelay(pdMS_TO_TICKS(POLL_MS));
        bool low = gpio_get_level(PIN_BOOT_BTN) == 0;
        if (low)
        {
            if (!pressed && ++stable_low >= DEBOUNCE_POLLS)
            {
                pressed = true;
                long_done = false;
                was_running = app_is_running();   // decided at press time, not release time
                t_down = xTaskGetTickCount();
            }
            if (pressed && !long_done && !was_running &&
                (xTaskGetTickCount() - t_down) >= pdMS_TO_TICKS(LONG_PRESS_MS))
            {
                app_mode_t m = app_mode();
                app_mode_t next = (m == MODE_CHECK) ? MODE_SELFTEST : (m == MODE_SELFTEST) ? MODE_RUN : MODE_CHECK;
                ESP_LOGI(TAG, "long press: %s -> %s", app_mode_name(m), app_mode_name(next));
                app_cmd_t c = { .kind = CMD_MODE, .mode = next, .sd = true, .text = "button" };
                app_request(&c);
                long_done = true;
            }
        }
        else
        {
            if (pressed && !long_done)
            {
                app_cmd_t c = { .kind = was_running ? CMD_STOP : CMD_BLINK_MODE };
                if (was_running) ESP_LOGI(TAG, "short press: stopping the run");
                app_request(&c);
            }
            pressed = false;
            stable_low = 0;
        }
    }
}

esp_err_t button_start(void)
{
    gpio_config_t in = {
        .pin_bit_mask = 1ULL << PIN_BOOT_BTN,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,       // board has a 10 k pull-up
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    esp_err_t err = gpio_config(&in);
    if (err != ESP_OK) return err;
    return xTaskCreate(button_task, "button", 3072, NULL, 3, NULL) == pdPASS ? ESP_OK : ESP_ERR_NO_MEM;
}
