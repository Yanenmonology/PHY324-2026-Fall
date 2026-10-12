#pragma once
#include <stdint.h>
#include <stddef.h>
#include "esp_err.h"
// Binary log little endian style, rec_hdr_t + payload
#define REC_FIFO 1 //payload n bytes of raw FIFIO w sensor in (0,12)
#define REC_SYNC 2 // no payload, seq = edge index, t_us = edge time
#define REC_STATUS 3 // payload = uint16 vbat_mV, uint16_t vbus_mV
#define REC_NOTE 4 // payload = ASCII text

typedef struct __attribute__((packed)) {
    uint8_t type;
    uint8_t sensor;
    uint16_t nbytes;
    uint32_t seq; // per sensor counter
    int64_t t_us; // esp timer time at read edge
}rec_hdr_t; //16 bytes, 8 words

esp_err_t sdlog_open(void); // mount sd card
void sdlog_close(void); // queue a. record for the writer task, return false if the ring is full
bool sdlog_put(const rec_hdr_t *h, const void *payload);
uint32_t sdlog_dropped(void);
