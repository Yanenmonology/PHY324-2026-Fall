// wifi.c — station-mode Wi-Fi + mDNS.

#include "wifi.h"
#include "secrets.h"

#include <string.h>

#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_log.h"
#include "mdns.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"

static const char *TAG = "wifi";

#define GOT_IP BIT0

static EventGroupHandle_t s_ev;

static void on_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    (void)arg;
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) 
    {
        esp_wifi_connect();
    } 
    else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) 
    {
        xEventGroupClearBits(s_ev, GOT_IP);
        vTaskDelay(pdMS_TO_TICKS(1000));
        esp_wifi_connect();
    } 
    else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP)
    {
        const ip_event_got_ip_t *e = (const ip_event_got_ip_t *)data;
        ESP_LOGI(TAG, "IP " IPSTR "  (also %s.local)", IP2STR(&e->ip_info.ip), ESP_HOSTNAME);
        xEventGroupSetBits(s_ev, GOT_IP);
    }
}

static void mdns_setup(void)
{
    if (mdns_init() != ESP_OK)
    {
        ESP_LOGW(TAG, "mDNS init failed; use the IP address instead");
        return;
    }
    mdns_hostname_set(ESP_HOSTNAME);
    mdns_instance_name_set("pendulum IMU bob");
    mdns_service_add(NULL, "_http", "_tcp", 80, NULL, 0);
}

bool wifi_connect_blocking(uint32_t timeout_ms)
{
    s_ev = xEventGroupCreate();
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, on_event, NULL, NULL));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, on_event, NULL, NULL));

    wifi_config_t wc = { 0 };
    strlcpy((char *)wc.sta.ssid, WIFI_SSID, sizeof wc.sta.ssid);
    strlcpy((char *)wc.sta.password, WIFI_PASS, sizeof wc.sta.password);
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wc));
    ESP_ERROR_CHECK(esp_wifi_start());

    mdns_setup();

    EventBits_t bits = xEventGroupWaitBits(s_ev, GOT_IP, pdFALSE, pdTRUE, pdMS_TO_TICKS(timeout_ms));
    if (!(bits & GOT_IP)) 
    {
        ESP_LOGW(TAG, "no IP after %lu ms; still retrying in the background",(unsigned long)timeout_ms);
        return false;
    }
    return true;
}

int wifi_rssi(void)
{
    wifi_ap_record_t ap;
    return esp_wifi_sta_get_ap_info(&ap) == ESP_OK ? ap.rssi : 0;
}
