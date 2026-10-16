#pragma once
#include <stdint.h>

void sync_start(int hz);
void sync_stop(void);

uint8_t sync_edge_count(void);
int sync_edge_time(uint32_t index, int64_t *t_us); //Fetch edge index t stamp, return 0 on success
int sync_edge_pop(uint32_t *index, int64_t *t_us); // Pop next not yet consumed edge