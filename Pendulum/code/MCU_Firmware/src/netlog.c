#include "netlog.h"
#include <stdarg.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include "esp_log.h"
#include "lwip/sockets.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define RING_SIZE  8192
#define LINE_MAX   192
#define PKT_MAX    1024

static char        s_ring[RING_SIZE];
static uint32_t    s_wr;          // total bytes ever written
static uint32_t    s_sent;        // total bytes sent to the current destination
static bool        s_have_dst;
static struct sockaddr_in s_dst;
static portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;
static vprintf_like_t s_orig;

static void ring_put(const char *p, size_t n)
{
    taskENTER_CRITICAL(&s_mux);
    for (size_t i = 0; i < n; i++)
    {
        s_ring[s_wr % RING_SIZE] = p[i];
        s_wr++;
    }
    taskEXIT_CRITICAL(&s_mux);
}

static int hook_vprintf(const char *fmt, va_list ap)
{
    va_list ap2;
    va_copy(ap2, ap);
    int n = (s_orig ? s_orig : vprintf)(fmt, ap);
    char line[LINE_MAX];
    int m = vsnprintf(line, sizeof line, fmt, ap2);
    va_end(ap2);
    if (m > 0) 
    {
        ring_put(line, m < LINE_MAX ? (size_t)m : LINE_MAX - 1);
    }
    return n;
}

int netlog_printf(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    int n = hook_vprintf(fmt, ap);
    va_end(ap);
    return n;
}

void netlog_subscribe(uint32_t ip_be, uint16_t port)
{
    taskENTER_CRITICAL(&s_mux);
    bool same = s_have_dst && s_dst.sin_addr.s_addr == ip_be && s_dst.sin_port == htons(port);
    if (!same)
    {
        memset(&s_dst, 0, sizeof s_dst);
        s_dst.sin_family = AF_INET;
        s_dst.sin_addr.s_addr = ip_be;
        s_dst.sin_port = htons(port);
        s_have_dst = true;
        s_sent = s_wr > RING_SIZE ? s_wr - RING_SIZE : 0;
    }
    taskEXIT_CRITICAL(&s_mux);
}

static void sender_task(void *arg)
{
    (void)arg;
    static char pkt[PKT_MAX];
    int sock = -1;

    for (;;)
    {
        vTaskDelay(pdMS_TO_TICKS(50));
        for (;;)
        {
            struct sockaddr_in dst;
            uint32_t start = 0;
            size_t   n = 0;

            taskENTER_CRITICAL(&s_mux);
            if (s_have_dst)
            {
                if (s_wr - s_sent > RING_SIZE) s_sent = s_wr - RING_SIZE;
                start = s_sent;
                n = s_wr - s_sent;
                if (n > PKT_MAX) n = PKT_MAX;
                for (size_t i = 0; i < n; i++) pkt[i] = s_ring[(start + i) % RING_SIZE];
                dst = s_dst;
            }
            taskEXIT_CRITICAL(&s_mux);
            if (n == 0) break;

            if (sock < 0) 
            {
                sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
            }
            if (sock >= 0) {
                sendto(sock, pkt, n, MSG_DONTWAIT, (struct sockaddr *)&dst, sizeof dst);
            }
            taskENTER_CRITICAL(&s_mux);
            if (s_sent == start) {s_sent += n;}
            taskEXIT_CRITICAL(&s_mux);
        }
    }
}

void netlog_init(void)
{
    s_orig = esp_log_set_vprintf(hook_vprintf);
    xTaskCreate(sender_task, "netlog", 3072, NULL, 2, NULL);
}
