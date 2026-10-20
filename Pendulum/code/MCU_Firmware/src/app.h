// The controller: one task owns the SPI bus and the sensors and runs exactly one mode at a
// time. Everything else (HTTP API, BOOT button, boot setting) only queues commands, so modes
// switch live, with no reboot and no SD-file corruption.
// Note:
//   0 idle      sensors powered down, Wi-Fi/OTA available
//   1 check     WHO_AM_I + free-running readout table every second (was stage 1)
//   2 selftest  ODR-triggered lock test, 10 s, then back to idle (was stage 2)
//   3 run       ODR-triggered acquisition -> SD and/or UDP stream, coded sync LEDs (was stage 3)
//   4 flicker   sync LEDs PWM at an exact frequency (rolling-shutter calibration)
#pragma once
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include "esp_err.h"

typedef enum {
    MODE_IDLE = 0, MODE_CHECK = 1, MODE_SELFTEST = 2, MODE_RUN = 3, MODE_FLICKER = 4, MODE_COUNT
} app_mode_t;

typedef enum { CMD_MODE, CMD_STOP, CMD_MARK, CMD_BLINK_MODE } app_cmd_kind_t;

typedef struct {
    app_cmd_kind_t kind;
    app_mode_t     mode;        // CMD_MODE
    uint16_t       mask;        // run: sensors to use (bit i = U(i+1)); 0 = all
    bool           sd;          // run: write to SD card
    int            hz;          // flicker frequency; 0 = default
    char           text[64];    // run label (CMD_MODE) or mark text (CMD_MARK)
} app_cmd_t;

esp_err_t   app_start(app_mode_t boot_mode);
esp_err_t   app_request(const app_cmd_t *c);   // queues; never blocks for long
app_mode_t  app_mode(void);
bool        app_is_running(void);              // true while mode == run
const char *app_mode_name(uint8_t mode);
int         app_status_json(char *buf, size_t n);
int         app_selftest_json(char *buf, size_t n);
