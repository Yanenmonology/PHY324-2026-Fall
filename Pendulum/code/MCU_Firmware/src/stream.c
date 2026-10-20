#include "stream.h"
#include "config.h"
#include <string.h>
#include "lwip/sockets.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/ringbuf.h"
#include "esp_log.h"
#include "esp_timer.h"

static const char *TAG = "stream";
#define FLUSH_US 20000          // send a part-filled packet after 20 ms

static RingbufHandle_t s_rb;
static portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;
static struct sockaddr_in s_dst;
static volatile bool s_active;
static volatile uint32_t s_drop;
static volatile uint32_t s_pkts;
static size_t s_ring_bytes;

size_t stream_ring_bytes(void) 
{ 
    return s_ring_bytes; 
}

static void sender(void *arg)
{
    (void)arg;
    static uint8_t pkt[STREAM_PKT_MAX];
    size_t fill = 0;
    uint32_t pkt_seq = 0;
    int64_t first_us = 0;
    int sock = -1;

    for (;;)
    {
        size_t n = 0;
        uint8_t *item = xRingbufferReceive(s_rb, &n, pdMS_TO_TICKS(10));
        int64_t now = esp_timer_get_time();
        bool flush = fill > 8 && (now - first_us > FLUSH_US || (item && fill + n > sizeof pkt));

        if (flush)
        {
            struct sockaddr_in dst;
            taskENTER_CRITICAL(&s_mux);
            dst = s_dst;
            bool active = s_active;
            taskEXIT_CRITICAL(&s_mux);
            if (active)
            {
                if (sock < 0) sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
                memcpy(pkt + 4, &pkt_seq, 4);
                if (sock < 0 || sendto(sock, pkt, fill, 0, (struct sockaddr *)&dst, sizeof dst) < 0)
                {
                    s_drop++; //failed packages: each holds several records
                }
                else
                {
                    s_pkts++;
                }
                pkt_seq++;//gap recorder
            }
            fill = 0;
        }
        if (item)
        {
            if (fill == 0)
            {
                pkt[0] = 'P'; pkt[1] = 'B'; pkt[2] = 'S'; pkt[3] = LOG_FORMAT_VERSION;
                fill = 8;
                first_us = now;
            }
            if (n <= sizeof pkt - fill)
            {
                memcpy(pkt + fill, item, n);
                fill += n;
            }
            else
            {
                s_drop++;       // cannot happen with FIFO chunks <= 140 words, kept as a guard
            }
            vRingbufferReturnItem(s_rb, item);
        }
    }
}

esp_err_t stream_init(void)
{
    if (s_rb) return ESP_OK;
    for (size_t sz = STREAM_RING_BYTES; sz >= 4 * 1024 && !s_rb; sz /= 2)
    {
        s_rb = xRingbufferCreate(sz, RINGBUF_TYPE_NOSPLIT);
        if (s_rb) s_ring_bytes = sz;
    }
    if (!s_rb) return ESP_ERR_NO_MEM;
    if (s_ring_bytes < STREAM_RING_BYTES) ESP_LOGW(TAG, "stream ring reduced to %u bytes", (unsigned)s_ring_bytes);
    if (xTaskCreatePinnedToCore(sender, "stream", 4096, NULL, 4, NULL, 0) != pdPASS) return ESP_ERR_NO_MEM;
    return ESP_OK;
}

void stream_subscribe(uint32_t ip_be, uint16_t port)
{
    taskENTER_CRITICAL(&s_mux);
    if (port == 0)
    {
        s_active = false;
    }
    else
    {
        memset(&s_dst, 0, sizeof s_dst);
        s_dst.sin_family = AF_INET;
        s_dst.sin_addr.s_addr = ip_be;
        s_dst.sin_port = htons(port);
        s_active = true;
    }
    taskEXIT_CRITICAL(&s_mux);
    ESP_LOGI(TAG, "%s", port ? "subscribed" : "unsubscribed");
}

bool stream_active(void) { return s_active; }

bool stream_put(const rec_hdr_t *h, const void *payload)
{
    if (!s_rb) return false;
    size_t total = sizeof *h + h->nbytes;
    void *dst;
    if (xRingbufferSendAcquire(s_rb, &dst, total, 0) != pdTRUE)
    {
        s_drop++;
        return false;
    }
    memcpy(dst, h, sizeof *h);
    if (h->nbytes) memcpy((uint8_t *)dst + sizeof *h, payload, h->nbytes);
    xRingbufferSendComplete(s_rb, dst);
    return true;
}

uint32_t stream_dropped(void) 
{ 
    return s_drop; 
}

uint32_t stream_packets(void) 
{ 
    return s_pkts; 
}