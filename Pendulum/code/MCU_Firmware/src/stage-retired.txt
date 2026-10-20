#include "stage.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "esp_log.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"


#define NVS_NS  "bob"
#define NVS_KEY "stage"

static const char *TAG = "stage";
static uint8_t s_current = 1;
static const char *const NAMES[STAGE_MAX + 1] = 
{
    [0] = "?", [1] = "id-check", [2] = "odr-selftest", [3] = "logging",
};

const char *stage_name(uint8_t id)
{
    return (id >= STAGE_MIN && id <= STAGE_MAX) ? NAMES[id] : NAMES[0];
}

uint8_t stage_current(void) 
{ 
    return s_current;
}

esp_err_t stage_init(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) 
    {
        ESP_LOGW(TAG, "[ERROR]NVS needs erase (%s)", esp_err_to_name(err));
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    if (err != ESP_OK) {return err;}
    nvs_handle_t h;
    if (nvs_open(NVS_NS, NVS_READONLY, &h) == ESP_OK) 
    {
        uint8_t v;
        if (nvs_get_u8(h, NVS_KEY, &v) == ESP_OK && v >= STAGE_MIN && v <= STAGE_MAX) 
        {
            s_current = v;
        }
        nvs_close(h);
    }
    ESP_LOGI(TAG, "boot stage %u (%s)", (unsigned)s_current, stage_name(s_current));
    return ESP_OK;
}

esp_err_t stage_select(uint8_t id)
{
    if (id < STAGE_MIN || id > STAGE_MAX) {return ESP_ERR_INVALID_ARG;}
    nvs_handle_t h;
    esp_err_t err = nvs_open(NVS_NS, NVS_READWRITE, &h);
    if (err != ESP_OK) {return err;}
    err = nvs_set_u8(h, NVS_KEY, id);
    if (err == ESP_OK) err = nvs_commit(h);
    nvs_close(h);
    if (err != ESP_OK) 
    {
        ESP_LOGE(TAG, "[ERROR]NVS write failed: %s", esp_err_to_name(err));
    }
    return err;
}

esp_err_t stage_select_and_reboot(uint8_t id)
{
    esp_err_t err = stage_select(id);
    if (err != ESP_OK) {return err;}
    ESP_LOGI(TAG, "rebooting into stage %u (%s)", (unsigned)id, stage_name(id));
    vTaskDelay(pdMS_TO_TICKS(300));
    esp_restart();
    
    return ESP_OK;
}