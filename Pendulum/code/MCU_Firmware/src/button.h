// BOOT button (GPIO0):
//   short press: during a run -> stop the run (file closed); otherwise blink the mode number
//   long press (>= 1.5 s, not during a run): next mode, live: check -> selftest -> run -> check
#pragma once
#include "esp_err.h"
esp_err_t button_start(void);
