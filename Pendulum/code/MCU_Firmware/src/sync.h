#pragma once
#include <stdint.h>

typedef enum { EDGE_SYNC = 0, EDGE_DRDY = 1, EDGE_NCH } edge_ch_t;
void sync_init(void);           // install the GPIO ISR service (once, at boot)
void sync_start(int hz);        // start the square wave + EDGE_SYNC capture
void sync_stop(void);
void drdy_arm(void);            // start EDGE_DRDY capture on PIN_IMU_INT
void drdy_disarm(void);

uint32_t edge_count(edge_ch_t ch);
int edge_time(edge_ch_t ch, uint32_t index, int64_t *t_us); 
    //Fetch edge index t stamp, return 0 on success
int edge_pop(edge_ch_t ch, uint32_t *index, int64_t *t_us); 
    // Pop next not yet consumed edge, return: 1 popped, 0 non