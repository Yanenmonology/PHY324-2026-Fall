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

esp_err_t sdlog_open(void)
{
    sdmmc_host_t host = SDMMC_HOST_DEFAULT();
    host.max_freq_khz = SDMMC_FREQ_HIGHSPEED; // note: 40MHz
    sdmmc_slot_config_t slot = SDMMC_SLOT_CONFIG_DEFAULT();
    slot.width = 4;
    slot.clk = PIN_SD_CLK;
    slot.cmd = PIN_SD_CMD;
    slot.d0 = PIN_SD_D0;
    slot.d1 = PIN_SD_D1;
    slot.d2 = PIN_SD_D2;
    slot.d3 = PIN_SD_D3;
    esp_vfs_fat_sdmmc_mount_config_t mc = {
        .format_if_mount_failed = false, .max_files = 3, .allocation_unit_size = 16 * 1024,
    };
    esp_err_t e = esp_vfs_fat_sdmmc_mount("/sdcard", &host, &slot, &mc, &s_card);
    if(e != ESP_OK)
    {
        ESP_LOGE(TAG, "[ERROR]mount failed: %s", esp_err_to_name(e));
        return e;
    }
    sdmmc_card_print_info(stdout, s_card);

    char path[40];
    int n = 0;
    struct stat st;
    do{
        snprintf(path, sizeof path, "/sdcard/run_%03d.bin", ++n);
    }
    while (stat(path,&st) == 0 && n < 999);
    s_f = fopen(path, "wb");
    if(!s_f)
    {
        ESP_LOGE(TAG, "cant create %s", path);
        return ESP_FAIL;
    }
    setvbuf(s_f, NULL, _IOFBF, 16 * 1024);
    uint8_t hdr[16] = {'P', 'I', 'M', 'U', 1 /*version*/, NUM_IMU, REF_HZ, 0};
    fwrite(hdr, 1, sizeof hdr, s_f);
    ESP_LOGI(TAG, "logging to %s", path);
    s_rb = xRingbufferCreate(RING_BYTES, RINGBUF_TYPE_NOSPLIT);
    s_run = true;
    xTaskCreatePinnedToCore(writer, "sdwriter", 6144, NULL, 3, &s_task, 0);
    return ESP_OK;
}

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

void sdlog_close(void)
{
    s_run = false;
    while(s_task)
    {
        vTaskDelay(pdMS_TO_TICKS(20));
    }
    esp_vfs_fat_sdcard_unmount("/sdcard", s_card);
}