#pragma once
#include <stdint.h>
#include <stdbool.h>
#include "esp_err.h" // from esp org
#include "pins.h"

// Address table, pg 52
#define R_IF_CFG         0x03
#define R_ODR_TRIG_CFG   0x06
#define R_INT1_CTRL      0x0D
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

#define R_FUNCTIONS_ENABLE 0x50
#define R_HAODR_CFG      0x62
#define R_FIFO_TAG       0x78

#define IF_CFG_I2C_I3C_DIS   0x01   // IF_CFG bit 0
#define FUNC_TIMESTAMP_EN    0x40   // FUNCTIONS_ENABLE bit 6
#define FIFO_MODE_CONTINUOUS 0x06   // FIFO_CTRL4 FIFO_MODE[2:0] = 110
#define INT1_DRDY_XL         0x01   // INT1_CTRL bit 0
#define FIFO_ST2_OVR_LATCHED 0x08   // FIFO_STATUS2 bit 3: FIFO overrun since last read

#define WHO_AM_I_VAL     0x70
// FIFO department
#define FIFO_WORD_BYTES  7
#define TAG_GY_NC        0x01
#define TAG_XL_NC        0x02
#define TAG_TIMESTAMP    0x04

void imu_cs_init(void);
esp_err_t imu_bus_init(void);
esp_err_t imu_read(int index, uint8_t reg, uint8_t *buf, int len);
esp_err_t imu_write(int index, uint8_t reg, uint8_t val);


void imu_disable_i2c_all(void); 
//need to check id
bool imu_init(int index);
void imu_start_free_running(int index);
void imu_power_down(int index);
//ODR + FIFO
void imu_start_triggered(int index);
//status
int imu_fifo_status(int index, uint8_t *status2);
void imu_enable_drdy_int1(int index);
//read n words
esp_err_t imu_fifo_read(int index, uint8_t *buf, int n);


static inline int16_t le16(const uint8_t *p)
{
    return (int16_t) (p[0] | (p[1] << 8));
}