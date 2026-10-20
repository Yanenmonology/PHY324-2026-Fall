// Live UDP stream object for the same records that go to the SD card (see rec.h).
// A laptop subscribes with POST /stream?port=P; records go to <its IP>:P, packed into
// data field of <= STREAM_PKT_MAX bytes: {'P','B','S',ver, uint32 pkt_seq} + records.
#pragma once
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include "esp_err.h"
#include "rec.h"
#define STREAM_PKT_MAX 1400 // remark no ip fragmentation
esp_err_t stream_init(void);
void      stream_subscribe(uint32_t ip_be, uint16_t port);   // port 0 = unsubscribe
bool      stream_active(void);
bool      stream_put(const rec_hdr_t *h, const void *payload); // false if the ring is full
uint32_t  stream_dropped(void);   // records lost on the ESP side (ring full or send failed)
uint32_t  stream_packets(void);
size_t    stream_ring_bytes(void);
