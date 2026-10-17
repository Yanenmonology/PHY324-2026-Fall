#pragma once
#include <stdbool.h>
#include <stdint.h>
bool wifi_connect_blocking(uint32_t timeout_ms);
int  wifi_rssi(void);