#include "esp_server.h"
#include "app.h"
#include "config.h"
#include "settings.h"
#include "netlog.h"
#include "stream.h"
#include "secret.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include "esp_http_server.h"
#include "esp_ota_ops.h"
#include "esp_system.h"
#include "esp_log.h"
#include "lwip/sockets.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "srv";

#define OTA_CHUNK 4096
#define OTA_MAX_TIMEOUTS 5
#define QMAX 256

// ---------------------------------------------------------------- helpers department
static esp_err_t send_json(httpd_req_t *req, const char *status, const char *json)
{
    if (status) httpd_resp_set_status(req, status);
    httpd_resp_set_type(req, "application/json");
    return httpd_resp_sendstr(req, json);
}

static bool authorized(httpd_req_t *req)
{
    char tok[64];
    if (httpd_req_get_hdr_value_str(req, "X-ota-Token", tok, sizeof tok) != ESP_OK) return false;
    return strcmp(tok, OTA_TOKEN) == 0;
}

#define REQUIRE_AUTH(req)                                                                  \
    do {                                                                                   \
        if (!authorized(req))                                                              \
            return send_json(req, "401 Unauthorized", "{\"error\":\"bad or missing token\"}"); \
    } while (0)

// whole query string ("" if none)
static void get_query(httpd_req_t *req, char *q, size_t n)
{
    q[0] = '\0';
    if (httpd_req_get_url_query_len(req) > 0) httpd_req_get_url_query_str(req, q, n);
}

// %XX and '+' decoding, then keep only characters that are safe inside JSON and the
// "key=value" config note. spaces_ok=false turns spaces into '_'.
static void decode_clean(const char *in, char *out, size_t n, bool spaces_ok)
{
    size_t o = 0;
    for (size_t i = 0; in[i] && o + 1 < n; i++)
    {
        char c = in[i];
        if (c == '+') c = ' ';
        else if (c == '%' && isxdigit((unsigned char)in[i + 1]) && isxdigit((unsigned char)in[i + 2]))
        {
            char hx[3] = { in[i + 1], in[i + 2], 0 };
            c = (char)strtol(hx, NULL, 16);
            i += 2;
        }
        if (c == ' ' && !spaces_ok) c = '_';
        if (!(isalnum((unsigned char)c) || strchr(" _-.,:+=#/()", c))) c = '_';
        out[o++] = c;
    }
    out[o] = '\0';
}

static bool q_str(const char *q, const char *key, char *out, size_t n, bool spaces_ok)
{
    char raw[96];
    if (httpd_query_key_value(q, key, raw, sizeof raw) != ESP_OK) return false;
    decode_clean(raw, out, n, spaces_ok);
    return true;
}

static bool q_long(const char *q, const char *key, long *out)
{
    char v[16];
    if (httpd_query_key_value(q, key, v, sizeof v) != ESP_OK) return false;
    char *end;
    *out = strtol(v, &end, 0);
    return end != v && *end == '\0';
}

static bool q_mask(const char *q, uint16_t *mask)
{
    char v[16];
    if (httpd_query_key_value(q, "mask", v, sizeof v) != ESP_OK) return false;
    if (!strcmp(v, "all")) { *mask = MASK_ALL; return true; }
    if (!strcmp(v, "stage1") || !strcmp(v, "s1")) { *mask = MASK_STAGE1; return true; }
    char *end;
    long m = strtol(v, &end, 0);
    if (end == v || *end || m <= 0 || m > MASK_ALL) return false;
    *mask = (uint16_t)m;
    return true;
}

static bool peer_ipv4(httpd_req_t *req, uint32_t *ip_be)
{
    struct sockaddr_storage ss;
    socklen_t len = sizeof ss;
    if (getpeername(httpd_req_to_sockfd(req), (struct sockaddr *)&ss, &len) != 0) return false;
    if (ss.ss_family == AF_INET)
    {
        *ip_be = ((struct sockaddr_in *)&ss)->sin_addr.s_addr;
        return true;
    }
#if LWIP_IPV6
    if (ss.ss_family == AF_INET6)
    {
        *ip_be = ((struct sockaddr_in6 *)&ss)->sin6_addr.un.u32_addr[3];   // IPv4-mapped
        return true;
    }
#endif
    return false;
}

static esp_err_t queue_cmd(httpd_req_t *req, const app_cmd_t *c, const char *ok_json)
{
    if (app_request(c) != ESP_OK)
        return send_json(req, "503 Service Unavailable", "{\"error\":\"controller busy, retry\"}");
    return send_json(req, NULL, ok_json);
}

// ---------------------------------------------------------------- action department
static esp_err_t status_get(httpd_req_t *req)
{
    char buf[1024];
    app_status_json(buf, sizeof buf);
    return send_json(req, NULL, buf);
}

static esp_err_t selftest_get(httpd_req_t *req)
{
    static char buf[2048];   // httpd runs one handler at a time
    app_selftest_json(buf, sizeof buf);
    return send_json(req, NULL, buf);
}

// shared by /mode, /stage and /run/start
static esp_err_t do_mode(httpd_req_t *req, long n, const char *q, bool save)
{
    if (n < 0 || n >= MODE_COUNT)
        return send_json(req, "400 Bad Request", "{\"error\":\"n must be 0 idle, 1 check, 2 selftest, 3 run, 4 flicker\"}");
    app_cmd_t c = { .kind = CMD_MODE, .mode = (app_mode_t)n, .sd = true };
    long v;
    if (q_long(q, "sd", &v)) c.sd = v != 0;
    if (q_long(q, "hz", &v)) c.hz = (int)v;
    if (strstr(q, "mask=") && !q_mask(q, &c.mask))
        return send_json(req, "400 Bad Request", "{\"error\":\"mask: 0x1..0x1FFF, all or stage1\"}");
    q_str(q, "label", c.text, sizeof c.text, false);
    if (save && settings_set_boot_mode((uint8_t)n) != ESP_OK)
        return send_json(req, "500 Internal Server Error", "{\"error\":\"NVS write failed\"}");
    char ok[128];
    snprintf(ok, sizeof ok, "{\"accepted\":true,\"mode\":\"%s\",\"saved\":%s}", app_mode_name((uint8_t)n),
             save ? "true" : "false");
    return queue_cmd(req, &c, ok);
}

static esp_err_t mode_post(httpd_req_t *req)
{
    REQUIRE_AUTH(req);
    char q[QMAX];
    get_query(req, q, sizeof q);
    long n, save = 0;
    if (!q_long(q, "n", &n)) return send_json(req, "400 Bad Request", "{\"error\":\"use /mode?n=0..4\"}");
    q_long(q, "save", &save);
    return do_mode(req, n, q, save != 0);
}

static esp_err_t stage_post(httpd_req_t *req)   // old API
{
    REQUIRE_AUTH(req);
    char q[QMAX];
    get_query(req, q, sizeof q);
    long n;
    if (!q_long(q, "n", &n) || n < 1 || n > 3)
        return send_json(req, "400 Bad Request", "{\"error\":\"use /stage?n=1..3\"}");
    return do_mode(req, n, q, true);
}

static esp_err_t run_start_post(httpd_req_t *req)
{
    REQUIRE_AUTH(req);
    char q[QMAX];
    get_query(req, q, sizeof q);
    if (app_is_running())
        return send_json(req, "409 Conflict", "{\"error\":\"already running; /run/stop first\"}");
    return do_mode(req, MODE_RUN, q, false);
}

static esp_err_t run_stop_post(httpd_req_t *req)
{
    REQUIRE_AUTH(req);
    if (!app_is_running()) return send_json(req, "409 Conflict", "{\"error\":\"not running\"}");
    app_cmd_t c = { .kind = CMD_STOP };
    return queue_cmd(req, &c, "{\"stopping\":true,\"note\":\"file is closed when /status shows running=false\"}");
}

static esp_err_t mark_post(httpd_req_t *req)
{
    REQUIRE_AUTH(req);
    char q[QMAX];
    get_query(req, q, sizeof q);
    app_cmd_t c = { .kind = CMD_MARK };
    if (!q_str(q, "text", c.text, sizeof c.text, true)) snprintf(c.text, sizeof c.text, "mark");
    if (!app_is_running()) return send_json(req, "409 Conflict", "{\"error\":\"not running\"}");
    return queue_cmd(req, &c, "{\"marked\":true}");
}

static esp_err_t flicker_post(httpd_req_t *req)
{
    REQUIRE_AUTH(req);
    char q[QMAX];
    get_query(req, q, sizeof q);
    if (app_is_running()) return send_json(req, "409 Conflict", "{\"error\":\"stop the run first\"}");
    return do_mode(req, MODE_FLICKER, q, false);
}

static esp_err_t subscribe_common(httpd_req_t *req, bool is_stream)
{
    REQUIRE_AUTH(req);
    char q[QMAX];
    get_query(req, q, sizeof q);
    long port;
    uint32_t ip;
    if (!q_long(q, "port", &port) || port < 0 || port > 65535)
        return send_json(req, "400 Bad Request", "{\"error\":\"use ?port=<udp port> (0 stops)\"}");
    if (!peer_ipv4(req, &ip)) return send_json(req, "500 Internal Server Error", "{\"error\":\"can't read client IP\"}");
    if (is_stream) stream_subscribe(ip, (uint16_t)port);
    else netlog_subscribe(ip, (uint16_t)port);
    return send_json(req, NULL, port ? "{\"subscribed\":true}" : "{\"subscribed\":false}");
}
static esp_err_t stream_post(httpd_req_t *req) { return subscribe_common(req, true); }
static esp_err_t log_post(httpd_req_t *req)    { return subscribe_common(req, false); }

static esp_err_t ota_post(httpd_req_t *req)
{
    REQUIRE_AUTH(req);
    if (app_is_running()) return send_json(req, "409 Conflict", "{\"error\":\"stop the run before updating\"}");
    if (req->content_len == 0) return send_json(req, "400 Bad Request", "{\"error\":\"empty body\"}");

    const esp_partition_t *target = esp_ota_get_next_update_partition(NULL);
    if (!target)
        return send_json(req, "500 Internal Server Error", "{\"error\":\"no OTA slot; check the partition table\"}");
    if (req->content_len > target->size)
        return send_json(req, "413 Payload Too Large", "{\"error\":\"image larger than OTA slot\"}");

    ESP_LOGI(TAG, "OTA: %u bytes -> %s", (unsigned)req->content_len, target->label);
    esp_ota_handle_t ota;
    esp_err_t err = esp_ota_begin(target, req->content_len, &ota);
    if (err != ESP_OK) return send_json(req, "500 Internal Server Error", "{\"error\":\"esp_ota_begin failed\"}");

    char *buf = malloc(OTA_CHUNK);
    if (!buf)
    {
        esp_ota_abort(ota);
        return send_json(req, "500 Internal Server Error", "{\"error\":\"out of memory\"}");
    }
    size_t remaining = req->content_len;
    int timeouts = 0;
    while (remaining > 0)
    {
        int r = httpd_req_recv(req, buf, remaining < OTA_CHUNK ? remaining : OTA_CHUNK);
        if (r == HTTPD_SOCK_ERR_TIMEOUT && ++timeouts < OTA_MAX_TIMEOUTS) continue;
        if (r <= 0) { err = ESP_FAIL; break; }
        timeouts = 0;
        err = esp_ota_write(ota, buf, r);
        if (err != ESP_OK) break;
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
        return send_json(req, "400 Bad Request", "{\"error\":\"image failed verification (corrupt or wrong chip)\"}");
    }
    err = esp_ota_set_boot_partition(target);
    if (err != ESP_OK) return send_json(req, "500 Internal Server Error", "{\"error\":\"set_boot_partition failed\"}");

    send_json(req, NULL, "{\"ok\":true,\"rebooting\":true}");
    ESP_LOGI(TAG, "OTA done, rebooting into %s", target->label);
    vTaskDelay(pdMS_TO_TICKS(500));
    esp_restart();
    return ESP_OK;
}

// ---------------------------------------------------------------- setting up
esp_err_t cmd_server_start(void)
{
    httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
    cfg.stack_size = 8192;
    cfg.recv_wait_timeout = 10;   // seconds per recv during OTA
    cfg.lru_purge_enable = true;
    cfg.max_uri_handlers = 16;

    httpd_handle_t srv = NULL;
    esp_err_t err = httpd_start(&srv, &cfg);
    if (err != ESP_OK)
    {
        ESP_LOGE(TAG, "httpd_start failed: %s", esp_err_to_name(err));
        return err;
    }
    static const httpd_uri_t uris[] = {
        { .uri = "/status",    .method = HTTP_GET,  .handler = status_get     },
        { .uri = "/selftest",  .method = HTTP_GET,  .handler = selftest_get   },
        { .uri = "/mode",      .method = HTTP_POST, .handler = mode_post      },
        { .uri = "/stage",     .method = HTTP_POST, .handler = stage_post     },
        { .uri = "/run/start", .method = HTTP_POST, .handler = run_start_post },
        { .uri = "/run/stop",  .method = HTTP_POST, .handler = run_stop_post  },
        { .uri = "/mark",      .method = HTTP_POST, .handler = mark_post      },
        { .uri = "/flicker",   .method = HTTP_POST, .handler = flicker_post   },
        { .uri = "/stream",    .method = HTTP_POST, .handler = stream_post    },
        { .uri = "/log",       .method = HTTP_POST, .handler = log_post       },
        { .uri = "/ota",       .method = HTTP_POST, .handler = ota_post       },
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
    esp_ota_img_states_t state;
    if (esp_ota_get_state_partition(running, &state) != ESP_OK || state != ESP_OTA_IMG_PENDING_VERIFY) return;
    if (healthy)
    {
        esp_ota_mark_app_valid_cancel_rollback();
        ESP_LOGI(TAG, "new firmware confirmed");
    }
    else
    {
        ESP_LOGE(TAG, "new firmware failed its boot check, rolling back");
        esp_ota_mark_app_invalid_rollback_and_reboot();
    }
}
