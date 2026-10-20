// VBAT/2 and VBUS/2 on ADC1 (works alongside Wi-Fi). Call only from the controller task.
#pragma once
void power_init(void);
int  power_vbat_mv(void);
int  power_vbus_mv(void);
