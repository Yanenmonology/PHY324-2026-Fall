#include "rec.h"
#include "config.h"
#include "pins.h"
#include "sdlog.h"
#include "stream.h"
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

void rec_file_header(uint8_t out[REC_FILE_HDR_BYTES])
{
    const uint8_t h[REC_FILE_HDR_BYTES] = {
        'P', 'I', 'M', 'U', LOG_FORMAT_VERSION, NUM_IMU, REF_HZ, ODR_TRIG_NODR,
        FS_XL_CODE, FS_G_CODE, TRIG_ODR_CODE, FIFO_BDR_CODE, HAODR_SEL, FIFO_TS_DEC, 0, 0,
    };
    memcpy(out, h, sizeof h);
}

void rec_put(const rec_hdr_t *h, const void *payload)
{
    if (sdlog_is_open()) sdlog_put(h, payload);
    if (stream_active()) stream_put(h, payload);
}

void rec_note(int64_t t_us, const char *fmt, ...)
{
    char text[200];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(text, sizeof text, fmt, ap);
    va_end(ap);
    if (n < 0) return;
    if (n >= (int)sizeof text) n = sizeof text - 1;
    rec_hdr_t h = { .type = REC_NOTE, .nbytes = (uint16_t)n, .t_us = t_us };
    rec_put(&h, text);
}
