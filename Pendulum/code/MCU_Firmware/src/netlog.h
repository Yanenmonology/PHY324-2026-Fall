#pragma once
#include <stdint.h>

void netlog_init(void);
int  netlog_printf(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
void netlog_subscribe(uint32_t ip_be, uint16_t port);