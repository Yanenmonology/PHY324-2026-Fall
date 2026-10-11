// All tunnable settings department. 
// Reference: github.com/STMicroelectronics/lsm6dsv16x-pid

#pragma once

#ifndef FW_STAGE
#define FW_STAGE 0.1
#endif

// SPI department:
#define SPI_CLK_HZ 8000000 //max is 10 MHz

// Sensor ranges department
#define FS_XL_CODE 1 //CTRL8 fs_xl: {0:2g, 1:4g, 2:8g, 3:16g}
#define FS_G_CODE 2  //CTRL6 fs_g: {1:250, 2:500, 3:1000, 4:20000}
#define XL_G_PER_LSB (0.122e-3f) //fit for \pm 4g
#define F_DPS_PER_LSB (17.5e-3f) //fir for \pm 500 dps

// Sample department:
//  - stage 1: 
#define FREE_ODR_CODE 6 // 120Hz, datasheet pg 13

//  - stage 2 &3:
// Note: samples per reference period = ODR_TRIG_NODR *2
#define USE_ODR_TRIGGER 1
#define REF_HZ 10 // tje sqare wave sent out by MCU
#define TRIG_ODR_CODE 0x7
#define HAODR_SEL 2 // Gyro on: \pm 1% , gyro off \pm 3%
#define ODR_TRIG_NODR 10 // 20 samples per period, 200 Hz at 10 Hz
#define FIFO_BDR_CODE // 0x7 pg60 binary code 0111

//Logging department
#define POLL_MS 100
#define RING_BYTES (96 * 1024)
#define FSYNC_EVERY_MS 2000
#define LOW_BATT_MV 3400