// record object for all data produced from mbs imu arrary and more
// Note: file header: 'P','I','M','U', version, NUM_IMU, REF_HZ, ODR_TRIG_NODR, FS_XL_CODE, FS_G_CODE,
//       TRIG_ODR_CODE, FIFO_BDR_CODE, HAODR_SEL, FIFO_TS_DEC, 0, 0
#pragma once
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
enum {
    REC_FIFO = 1, // sensor = 0..12; seq = per-sensor record counter; t_us = time just BEFORE
                    // the FIFO_STATUS read of this burst (same for every chunk of one burst);
                    // payload = raw FIFO words (7 bytes each: tag + 6 data)
    REC_SYNC = 2, // IMU_SYNC rising edge: seq = edge index, t_us = edge time. No payload.
    REC_STATUS = 3, // payload = rec_status_t
    REC_NOTE = 4, // payload = ASCII text (run start/stop, user marks, errors)
    REC_LED = 5, // sync-LED edge: sensor = new state (1 on, 0 off), seq = edge index
    REC_DRDY = 6, // U13 INT1 data-ready pulse: seq = pulse index, t_us = pulse time
    REC_ARM = 7, // sensor = i, t_us = when sensor i was switched into ODR-triggered mode
    REC_CONFIG = 8, // payload = 16-byte file header + ASCII "key=value ..." (repeated every 2 s)
    REC_FIFOST = 9, // sensor = i, seq = FIFO_STATUS2 byte, t_us = when an overrun was seen
};
typedef struct __attribute__((packed)) {
    uint8_t  type;
    uint8_t  sensor;
    uint16_t nbytes;
    uint32_t seq;
    int64_t  t_us;      // esp_timer time (systimer, 40 MHz crystal)
} rec_hdr_t;            // 16 bytes

typedef struct __attribute__((packed)) {
    uint16_t vbat_mv;
    uint16_t vbus_mv;
    uint32_t sd_drops;
    uint32_t stream_drops;
    int8_t   rssi_dbm;
    uint8_t  mode;
    uint16_t fifo_ovr;  // total FIFO overruns seen this run, all sensors
} rec_status_t;         // 16 bytes
#define REC_FILE_HDR_BYTES 16
void rec_file_header(uint8_t out[REC_FILE_HDR_BYTES]);
// Remark: Fan-out: queue to the SD writer (if a file is open) and the UDP stream (if subscribed).
void rec_put(const rec_hdr_t *h, const void *payload);
void rec_note(int64_t t_us, const char *fmt, ...) __attribute__((format(printf, 2, 3)));