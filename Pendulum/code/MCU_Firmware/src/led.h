#pragma once
#include <stdint.h>

void led_init(void);
void led_code_start(void);
void led_code_stop(void);
void led_flicker_start(int hz);
void led_flicker_stop(void);
// Pop the next coded-train edge. Returns 1 if popped. state: 1 = LEDs on.
int led_edge_pop(uint32_t *index, uint8_t *state, int64_t *t_us);