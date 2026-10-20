#pragma once
#include <stdbool.h>
#include "esp_err.h"

esp_err_t cmd_server_start(void);
void ota_confirm_boot(bool healthy);