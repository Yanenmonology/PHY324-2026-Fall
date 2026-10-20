//Add choosing the stage over wifi server on ESP 32, this is for button
#pragma once
#include <stdint.h>
#include <stdbool.h>
#include "esp_err.h"

#define STAGE_MIN 1
#define STAGE_MAX 3

// remark: 1:ID check + free-run readout, 2:ODR-trigger self-test, 3:sd card logging department.
esp_err_t   stage_init(void);
uint8_t     stage_current(void);          // stage this boot is running
const char *stage_name(uint8_t id);       // "id-check", "odr-selftest", "logging"
esp_err_t   stage_select(uint8_t id);
esp_err_t   stage_select_and_reboot(uint8_t id);
