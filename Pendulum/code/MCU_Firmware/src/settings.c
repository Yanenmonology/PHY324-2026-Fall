//new stage
#include "settings.h"
#include "app.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "esp_log.h"

#define NVS_NS  "mbs"
#define NVS_KEY "stage"

static const char *TAG = "settings";
static uint8_t s_boot_mode = MODE_IDLE;

esp_err_t settings_init(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND)
    {
        ESP_LOGW(TAG, "NVS needs erase (%s)", esp_err_to_name(err));
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    if (err != ESP_OK) return err;
    nvs_handle_t h;
    if (nvs_open(NVS_NS, NVS_READONLY, &h) == ESP_OK)
    {
        uint8_t v;
        if (nvs_get_u8(h, NVS_KEY, &v) == ESP_OK && v < MODE_COUNT) s_boot_mode = v;
        nvs_close(h);
    }
    ESP_LOGI(TAG, "boot mode %u (%s)", (unsigned)s_boot_mode, app_mode_name(s_boot_mode));
    return ESP_OK;
}

uint8_t settings_boot_mode(void) { return s_boot_mode; }

esp_err_t settings_set_boot_mode(uint8_t mode)
{
    if (mode >= MODE_COUNT) return ESP_ERR_INVALID_ARG;
    nvs_handle_t h;
    esp_err_t err = nvs_open(NVS_NS, NVS_READWRITE, &h);
    if (err != ESP_OK) return err;
    err = nvs_set_u8(h, NVS_KEY, mode);
    if (err == ESP_OK) err = nvs_commit(h);
    nvs_close(h);
    if (err == ESP_OK) s_boot_mode = mode;
    else ESP_LOGE(TAG, "NVS write failed: %s", esp_err_to_name(err));
    return err;
}
