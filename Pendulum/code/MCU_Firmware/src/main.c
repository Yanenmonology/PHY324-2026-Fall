// Pendulum IMU bob firmware (ESP32-S3-WROOM-1-N8, 13 x LSM6DSV16X).
// Boot order matters: CS lines high and I2C off on every sensor before anything else touches
// the SPI bus (PCB rev A firmware notes 1-2). After that everything runs in the controller
// (app.c) and is driven over Wi-Fi (esp_server.h lists the API) or the BOOT button.
#include "config.h"
#include "pins.h"
#include "imu.h"
#include "sync.h"
#include "led.h"
#include "sdlog.h"
#include "stream.h"
#include "netlog.h"
#include "wifi.h"
#include "settings.h"
#include "esp_server.h"
#include "button.h"
#include "app.h"

#include "esp_log.h"
#include "esp_app_desc.h"
#include "esp_system.h"

static const char *TAG = "main";

void app_main(void)
{
    imu_cs_init(); // all 13 CS high (no external pull-ups on the board)
    led_init(); // sync-LED gate high = off
    ESP_ERROR_CHECK(imu_bus_init());
    imu_disable_i2c_all();          // first SPI traffic: IF_CFG on every sensor
    ESP_ERROR_CHECK(settings_init()); // NVS (Wi-Fi needs it) + saved boot mode
    sync_init();
    netlog_init();

    ESP_LOGI(TAG, "fw %s", esp_app_get_description()->version);
    bool wifi_ok = wifi_connect_blocking(15000);
    bool srv_ok = cmd_server_start() == ESP_OK;
    // rings after Wi-Fi + HTTP server, so those get their RAM first; sizes shrink if needed
    bool rings_ok = sdlog_init() == ESP_OK && stream_init() == ESP_OK;
    ESP_LOGI(TAG, "rings: SD %u B, stream %u B, free heap %u B", (unsigned)sdlog_ring_bytes(),
             (unsigned)stream_ring_bytes(), (unsigned)esp_get_free_heap_size());
    ota_confirm_boot(wifi_ok && srv_ok && rings_ok);

    ESP_ERROR_CHECK(app_start((app_mode_t)settings_boot_mode()));
    button_start();
}
