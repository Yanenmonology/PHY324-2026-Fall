#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include "esp_err.h"
#include "rec.h"
// Binary log little endian style, rec_hdr_t + payload
#define REC_FIFO 1 //payload n bytes of raw FIFIO w sensor in (0,12)
#define REC_SYNC 2 // no payload, seq = edge index, t_us = edge time
#define REC_STATUS 3 // payload = uint16 vbat_mV, uint16_t vbus_mV
#define REC_NOTE 4 // payload = ASCII text
//Move rec struct to rec.h for expansion
esp_err_t sdlog_init(void);                          // allocate the ring once at boot
size_t    sdlog_ring_bytes(void);
esp_err_t sdlog_open(char *name_out, size_t name_len); // mount + create next run_NNN.bin
bool      sdlog_is_open(void);
bool      sdlog_put(const rec_hdr_t *h, const void *payload); // false if the ring is full
void      sdlog_close(void);                         // drain, fsync, close, unmount
uint32_t  sdlog_dropped(void);
uint32_t  sdlog_write_errors(void);