#pragma once
#include <stdbool.h>
#include "esp_err.h"

typedef struct {
    bool      (*is_logging)(void);
    esp_err_t (*stop_run)(void);
} cmd_hooks_t;
//HTTP server department.
esp_err_t cmd_server_start(const cmd_hooks_t *hooks);
void ota_confirm_boot(bool healthy);