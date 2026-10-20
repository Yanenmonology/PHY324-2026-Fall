// stage - stage button
#pragma once
#include <stdbool.h>
#include "esp_err.h"
#define BOOT_BUTTON_GPIO 0
#define STATUS_LED_GPIO 21
#define STATUS_LED_ON_LEVEL 1

esp_err_t stage_button_start(int led_gpio, bool (*is_logging)(void));