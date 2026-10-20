#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include<unistd.h>
#include "sdlog.h"
#include "config.h"
#include "pins.h"
#include "esp_vfs_fat.h"
#include "sdmmc_cmd.h"
#include "driver/sdmmc_host.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/ringbuf.h"
#include "esp_log.h"
#include "esp_timer.h"

static const char *TAG = "sdlog";
static RingbufHandle_t s_rb;
static FILE *s_f;
static sdmmc_card_t *s_card;
static volatile bool s_run;
static TaskHandle_t s_task;
static volatile uint32_t s_drop;
static volatile bool s_open;// producer side: rec_put() may queue
static volatile uint32_t s_werr;

static void writer(void *arg)
{
    int64_t last_sync = esp_timer_get_time();
    while(1)
    {
        size_t n;
        void *p = xRingbufferReceive(s_rb, &n, pdMS_TO_TICKS(100));
        if (p)
        {
            fwrite(p, 1, n, s_f);
            vRingbufferReturnItem(s_rb, p);
        }
        if((esp_timer_get_time() - last_sync) > FSYNC_EVERY_MS * 1000LL)
        {
            fflush(s_f);
            fsync(fileno(s_f));
            last_sync = esp_timer_get_time();
        }
        if(!s_run && !p)
        {
            break;
        }
    }
    fflush(s_f);
    fsync(fileno(s_f));
    fclose(s_f);
    s_f = NULL;
    s_task = NULL;
    vTaskDelete(NULL);
}

static size_t s_ring_bytes;

esp_err_t sdlog_init(void)
{
    if (s_rb) return ESP_OK;
    for (size_t sz = SD_RING_BYTES; sz >= 8 * 1024 && !s_rb; sz /= 2)
    {
        s_rb = xRingbufferCreate(sz, RINGBUF_TYPE_NOSPLIT);
        if (s_rb) s_ring_bytes = sz;
    }
    if (!s_rb) ESP_LOGE(TAG, "no RAM for the SD ring");
    else if (s_ring_bytes < SD_RING_BYTES) ESP_LOGW(TAG, "SD ring reduced to %u bytes", (unsigned)s_ring_bytes);
    return s_rb ? ESP_OK : ESP_ERR_NO_MEM;
}
size_t sdlog_ring_bytes(void) 
{ 
    return s_ring_bytes; 
}

esp_err_t sdlog_open(char *name_out, size_t name_len)
{
    if (!s_rb) return ESP_ERR_NO_MEM;
    if (s_open || s_task) return ESP_ERR_INVALID_STATE;

    sdmmc_host_t host = SDMMC_HOST_DEFAULT();
    host.max_freq_khz = SDMMC_FREQ_DEFAULT;   // 20 MHz: ~100x the ~60 KB/s we need
    sdmmc_slot_config_t slot = SDMMC_SLOT_CONFIG_DEFAULT();
    slot.width = 4;
    slot.clk = PIN_SD_CLK;
    slot.cmd = PIN_SD_CMD;
    slot.d0 = PIN_SD_D0;
    slot.d1 = PIN_SD_D1;
    slot.d2 = PIN_SD_D2;
    slot.d3 = PIN_SD_D3;
    esp_vfs_fat_sdmmc_mount_config_t mc = 
    {
        .format_if_mount_failed = false, .max_files = 3, .allocation_unit_size = 16 * 1024,
    };
    esp_err_t e = esp_vfs_fat_sdmmc_mount("/sdcard", &host, &slot, &mc, &s_card);
    if (e != ESP_OK)
    {
        ESP_LOGE(TAG, "mount failed: %s", esp_err_to_name(e));
        return e;
    }
    char path[40];
    int n = 0;
    struct stat st;
    do {
        snprintf(path, sizeof path, "/sdcard/run_%03d.bin", ++n);
    } while (stat(path, &st) == 0 && n < 999);
    s_f = fopen(path, "wb");
    if (!s_f)
    {
        ESP_LOGE(TAG, "can't create %s", path);
        esp_vfs_fat_sdcard_unmount("/sdcard", s_card);
        return ESP_FAIL;
    }
    setvbuf(s_f, NULL, _IOFBF, 16 * 1024);
    uint8_t hdr[REC_FILE_HDR_BYTES]; // need update 
    rec_file_header(hdr);
    fwrite(hdr, 1, sizeof hdr, s_f);
    if (name_out) snprintf(name_out, name_len, "%s", path + strlen("/sdcard/"));
    ESP_LOGI(TAG, "logging to %s", path);
    size_t junk; // from previous run
    void *p;
    while ((p = xRingbufferReceive(s_rb, &junk, 0)) != NULL) vRingbufferReturnItem(s_rb, p);
    s_drop = 0;
    s_werr = 0;
    s_run = true;
    if (xTaskCreatePinnedToCore(writer, "sdwriter", 6144, NULL, 3, (TaskHandle_t *)&s_task, 0) != pdPASS)
    {
        fclose(s_f);
        s_f = NULL;
        esp_vfs_fat_sdcard_unmount("/sdcard", s_card);
        return ESP_ERR_NO_MEM;
    }
    s_open = true;
    return ESP_OK;
}

bool sdlog_is_open(void) { return s_open; }

bool sdlog_put(const rec_hdr_t *h, const void *payload)
{
    size_t total = sizeof *h + h->nbytes;
    void *dst;
    if(xRingbufferSendAcquire(s_rb, &dst, total, 0) != pdTRUE)
    {
        s_drop ++;
        return false;
    }
    memcpy(dst, h, sizeof *h);
    if(h->nbytes)
    {
        memcpy((uint8_t *)dst + sizeof *h, payload, h->nbytes);
    }
    xRingbufferSendComplete(s_rb, dst);
    return true;
}

uint32_t sdlog_dropped(void)
{ 
    return s_drop; 
}

uint32_t sdlog_write_errors(void) 
{ 
    return s_werr; 
}

void sdlog_close(void)
{
    if (!s_open) return;
    s_open = false;
    s_run = false;
    while (s_task) vTaskDelay(pdMS_TO_TICKS(20));
    esp_vfs_fat_sdcard_unmount("/sdcard", s_card);
    ESP_LOGI(TAG, "file closed, card unmounted");
}