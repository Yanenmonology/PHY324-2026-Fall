#include "esp_server.h"
#include "stage.h"
#include "netlog.h"
#include "wifi.h"
#include "secret.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "esp_http_server.h"
#include "esp_ota_ops.h"
#include "esp_app_desc.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_log.h"
#include "lwip/sockets.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "srv";

#define OTA_CHUNK 4096
#define OTA_MAX_TIMEOUTS 5

static cmd_hooks_t s_hooks;

//helpers department

static esp_err_t send_json(httpd_req_t *req, const char *status, const char *json)
{
    if (status) 
    {
        httpd_resp_set_status(req, status);
    }
    httpd_resp_set_type(req, "application/json");
    return httpd_resp_sendstr(req, json);
}

static bool authorized(httpd_req_t *req)
{
    char tok[64];
    if (httpd_req_get_hdr_value_str(req, "X-ota-Token", tok, sizeof tok) != ESP_OK) 
    {
        return false;
    }
    return strcmp(tok, OTA_TOKEN) == 0;
}

#define REQUIRE_AUTH(req)                                                    \
    do {                                                                     \
        if (!authorized(req))                                                \
            return send_json(req, "401 Unauthorized", "{\"error\":\"bad or missing token\"}"); \
    } while (0)

static bool logging_now(void)
{
    return s_hooks.is_logging && s_hooks.is_logging();
}

static bool query_long(httpd_req_t *req, const char *key, long *out)
{
    char q[48], v[12];
    if (httpd_req_get_url_query_str(req, q, sizeof q) != ESP_OK || httpd_query_key_value(q, key, v, sizeof v) != ESP_OK) 
    {
        return false;
    }
    char *end;
    *out = strtol(v, &end, 10);
    return end != v && *end == '\0';
}

static bool peer_ipv4(httpd_req_t *req, uint32_t *ip_be)
{
    struct sockaddr_storage ss;
    socklen_t len = sizeof ss;
    if (getpeername(httpd_req_to_sockfd(req), (struct sockaddr *)&ss, &len) != 0) {return false;}
    if (ss.ss_family == AF_INET) 
    {
        *ip_be = ((struct sockaddr_in *)&ss)->sin_addr.s_addr;
        return true;
    }
#if LWIP_IPV6
    if (ss.ss_family == AF_INET6) 
    {
        *ip_be = ((struct sockaddr_in6 *)&ss)->sin6_addr.un.u32_addr[3];
        return true;
    }
#endif
    return false;
}

//action department

static esp_err_t status_get(httpd_req_t *req)
{
    const esp_app_desc_t  *app  = esp_app_get_description();
    const esp_partition_t *part = esp_ota_get_running_partition();
    uint8_t st = stage_current();

    char buf[384];
    snprintf(buf, sizeof buf,
             "{\"fw\":\"%s\",\"built\":\"%s %s\",\"partition\":\"%s\","
             "\"stage\":%u,\"stage_name\":\"%s\",\"logging\":%s,"
             "\"rssi_dbm\":%d,\"uptime_s\":%lld,\"free_heap\":%lu}",
             app->version, app->date, app->time, part ? part->label : "?",
             (unsigned)st, stage_name(st), logging_now() ? "true" : "false",
             wifi_rssi(), (long long)(esp_timer_get_time() / 1000000),
             (unsigned long)esp_get_free_heap_size());
    return send_json(req, NULL, buf);
}

static esp_err_t stage_post(httpd_req_t *req)
{
    REQUIRE_AUTH(req);
    long n;
    if (!query_long(req, "n", &n) || n < STAGE_MIN || n > STAGE_MAX)
    {
        return send_json(req, "400 Bad Request", "{\"error\":\"use /stage?n=1..3\"}");
    }
    if (logging_now())
    {
        return send_json(req, "409 Conflict",
                         "{\"error\":\"logging; stop the run first so the SD file is closed\"}");
    }
    if (stage_select((uint8_t)n) != ESP_OK)
    {
        return send_json(req, "500 Internal Server Error", "{\"error\":\"NVS write failed\"}");
    }
    char buf[96];
    snprintf(buf, sizeof buf, "{\"stage\":%ld,\"name\":\"%s\",\"rebooting\":true}",
             n, stage_name((uint8_t)n));
    send_json(req, NULL, buf);
    stage_select_and_reboot((uint8_t)n);
    return ESP_OK;
}

static esp_err_t run_stop_post(httpd_req_t *req)
{
    REQUIRE_AUTH(req);
    if (!s_hooks.stop_run)
    {
        return send_json(req, "501 Not Implemented", "{\"error\":\"no stop_run hook\"}");
    }
    if (!logging_now())
    {
        return send_json(req, "409 Conflict", "{\"error\":\"not logging\"}");
    }
    s_hooks.stop_run();
    return send_json(req, NULL,"{\"stopping\":true,\"note\":\"file is closed when status shows logging=false\"}");
}

static esp_err_t log_post(httpd_req_t *req)
{
    REQUIRE_AUTH(req);
    long port;
    uint32_t ip;
    if (!query_long(req, "port", &port) || port < 1 || port > 65535)
    {
        return send_json(req, "400 Bad Request", "{\"error\":\"use /log?port=<udp port>\"}");
    }
    if (!peer_ipv4(req, &ip))
    {
        return send_json(req, "500 Internal Server Error", "{\"error\":\"can't read client IP\"}");
    }
    netlog_subscribe(ip, (uint16_t)port);
    return send_json(req, NULL, "{\"subscribed\":true}");
}

static esp_err_t ota_post(httpd_req_t *req)
{
    REQUIRE_AUTH(req);
    if (logging_now())
    {
        return send_json(req, "409 Conflict", "{\"error\":\"stop the run before updating\"}");
    }
    if (req->content_len == 0)
    {
        return send_json(req, "400 Bad Request", "{\"error\":\"empty body\"}");
    }

    const esp_partition_t *target = esp_ota_get_next_update_partition(NULL);
    if (!target)
    {
        return send_json(req, "500 Internal Server Error",
                         "{\"error\":\"no OTA slot; check the partition table\"}");
    }
    if (req->content_len > target->size)
    {
        return send_json(req, "413 Payload Too Large", "{\"error\":\"image larger than OTA slot\"}");
    }

    ESP_LOGI(TAG, "OTA: %u bytes -> %s", (unsigned)req->content_len, target->label);

    esp_ota_handle_t ota;
    esp_err_t err = esp_ota_begin(target, req->content_len, &ota);
    if (err != ESP_OK)
    {
        return send_json(req, "500 Internal Server Error", "{\"error\":\"esp_ota_begin failed\"}");
    }

    char *buf = malloc(OTA_CHUNK);
    if (!buf)
    {
        esp_ota_abort(ota);
        return send_json(req, "500 Internal Server Error", "{\"error\":\"out of memory\"}");
    }

    size_t remaining = req->content_len;
    int    timeouts  = 0;
    while (remaining > 0)
    {
        int r = httpd_req_recv(req, buf, remaining < OTA_CHUNK ? remaining : OTA_CHUNK);
        if (r == HTTPD_SOCK_ERR_TIMEOUT && ++timeouts < OTA_MAX_TIMEOUTS) {continue;}
        if (r <= 0) { err = ESP_FAIL; break; }
        timeouts = 0;
        err = esp_ota_write(ota, buf, r);
        if (err != ESP_OK) {break;}
        remaining -= r;
    }
    free(buf);

    if (err != ESP_OK)
    {
        esp_ota_abort(ota);
        ESP_LOGE(TAG, "OTA upload failed: %s", esp_err_to_name(err));
        return send_json(req, "500 Internal Server Error", "{\"error\":\"upload failed\"}");
    }
    err = esp_ota_end(ota);
    if (err != ESP_OK)
    {
        ESP_LOGE(TAG, "OTA image invalid: %s", esp_err_to_name(err));
        return send_json(req, "400 Bad Request",
                         "{\"error\":\"image failed verification (corrupt or wrong chip)\"}");
    }
    err = esp_ota_set_boot_partition(target);
    if (err != ESP_OK)
    {
        return send_json(req, "500 Internal Server Error", "{\"error\":\"set_boot_partition failed\"}");
    }

    send_json(req, NULL, "{\"ok\":true,\"rebooting\":true}");
    ESP_LOGI(TAG, "OTA done, rebooting into %s", target->label);
    vTaskDelay(pdMS_TO_TICKS(500));
    esp_restart();
    return ESP_OK;
}

// setting up

esp_err_t cmd_server_start(const cmd_hooks_t *hooks)
{
    if (hooks) s_hooks = *hooks;
    httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
    cfg.stack_size = 8192;
    cfg.recv_wait_timeout = 10;   // seconds per recv during OTA
    cfg.lru_purge_enable = true;

    httpd_handle_t srv = NULL;
    esp_err_t err = httpd_start(&srv, &cfg);
    if (err != ESP_OK)
    {
        ESP_LOGE(TAG, "httpd_start failed: %s", esp_err_to_name(err));
        return err;
    }

    static const httpd_uri_t uris[] =
    {
        { .uri = "/status",   .method = HTTP_GET,  .handler = status_get    },
        { .uri = "/stage",    .method = HTTP_POST, .handler = stage_post    },
        { .uri = "/run/stop", .method = HTTP_POST, .handler = run_stop_post },
        { .uri = "/log",      .method = HTTP_POST, .handler = log_post      },
        { .uri = "/ota",      .method = HTTP_POST, .handler = ota_post      },
    };
    for (size_t i = 0; i < sizeof uris / sizeof uris[0]; i++)
    {
        err = httpd_register_uri_handler(srv, &uris[i]);
        if (err != ESP_OK) return err;
    }
    ESP_LOGI(TAG, "command server on port %d", cfg.server_port);
    return ESP_OK;
}

void ota_confirm_boot(bool healthy)
{
    const esp_partition_t *running = esp_ota_get_running_partition();
    esp_ota_img_states_t   state;
    if (esp_ota_get_state_partition(running, &state) != ESP_OK || state != ESP_OTA_IMG_PENDING_VERIFY)
    {
        return;
    }
    if (healthy)
    {
        esp_ota_mark_app_valid_cancel_rollback();
        ESP_LOGI(TAG, "new firmware confirmed");
    } else
    {
        ESP_LOGE(TAG, "new firmware failed its boot check, rolling back");
        esp_ota_mark_app_invalid_rollback_and_reboot();
    }
}
