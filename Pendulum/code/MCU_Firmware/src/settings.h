// Persistent settings in NVS (namespace "mbs"). Also initialises NVS, which Wi-Fi needs.
#pragma once
#include <stdint.h>
#include "esp_err.h"

esp_err_t settings_init(void);
uint8_t   settings_boot_mode(void);            // mode entered at power-up (default 0 = idle)
esp_err_t settings_set_boot_mode(uint8_t mode);