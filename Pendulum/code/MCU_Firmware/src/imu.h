#pragma once
#include <stdint.h>
#include <stdbool.h>
#include "esp_err.h" // from esp org
#include "pins.h"

// Address table, pg 52
#define R_IF_CFG         0x03
#define R_ODR_TRIG_CFG   0x06
#define R_WHO_AM_I       0x0f

#define R_FIFO_CTRL1     0x07   
#define R_FIFO_CTRL2     0x08
#define R_FIFO_CTRL3     0x09
#define R_FIFO_CTRL4     0x0A

#define R_CTRL1          0x10
#define R_CTRL2          0x11
#define R_CTRL3          0x12
#define R_CTRL4          0x13
#define R_CTRL5          0x14
#define R_CTRL6          0x15
#define R_CTRL7          0x16
#define R_CTRL8          0x17

#define R_FIFO_STATUS1   0x1B
#define R_FIFO_STATUS2   0x1C
#define R_STATUS         0x1E

#define R_OUTX_L_G       0x22 //nvm, brust read same example i reference all axis are read
#define R_OUTX_L_A       0x28

#define R_HAODR_CFG      0x62
#define R_WHO_AM_I_VAL   0x70
#define R_FIFO_TAG       0x78
// FIFO department
#define FIFO_WORD_BYTES  7
#define TAG_GY_NC        0x01
#define TAG_XL_NC        0x02
#define TAG_TIMESTAMP    0x04

void imu_cs_init(void);
esp_err_t imu_bus_init