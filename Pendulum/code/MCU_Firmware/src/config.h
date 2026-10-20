// All tunnable settings department. 
// Reference: github.com/STMicroelectronics/lsm6dsv16x-pid

#pragma once

//abundon
#ifndef FW_STAGE
#define FW_STAGE 0.1
#endif

// SPI department:
#define SPI_CLK_HZ 8000000 //max is 10 MHz

// Sensor ranges department
#define FS_XL_CODE 1 //CTRL8 fs_xl: {0:2g, 1:4g, 2:8g, 3:16g}
#define FS_G_CODE 2  //CTRL6 fs_g: {1:250, 2:500, 3:1000, 4:20000}
#define XL_G_PER_LSB (0.122e-3f) //fit for \pm 4g
#define G_DPS_PER_LSB (17.5e-3f) //fir for \pm 500 dps

// Sample department:
//  - stage 1: 
#define FREE_ODR_CODE 6 // 120Hz, datasheet pg 13

//  - stage 2 &3:
// Note: samples per reference period = ODR_TRIG_NODR *2
#define USE_ODR_TRIGGER 1
#define REF_HZ 10 // tje sqare wave sent out by MCU(40MHZ,on pin 48)
#define TRIG_ODR_CODE 0x7 //within DT0155's +-33 % of the target
#define HAODR_SEL 2 // Gyro on: \pm 1% , gyro off \pm 3%
#define ODR_TRIG_NODR 10 // 20 samples per period, 200 Hz at 10 Hz
#define FIFO_BDR_CODE 0x7// 0x7 pg60 binary code 0111
#define FIFO_TS_DEC 2 // Remark: FIFO_CTRL4 DEC_TS_BATCH: 1 = timestamp every sample, 2 = every 8th, 3 = every 32nd. 8th: ~30 % less data than
                      // every sample; the host still bridges gaps with them.
#define SAMPLES_PER_REF     (2 * ODR_TRIG_NODR)         

//General purpose:
//Logging department
#define POLL_MS 100
#define POLL_JITTER_MS 4 //remark: random 0..N ms added per sweep
#define SD_RING_BYTES (96 * 1024)
#define STREAM_RING_BYTES (24 * 1024)
#define FSYNC_EVERY_MS 2000
#define CONFIG_EVERY_MS 2000 // re-send the config record so late stream listeners get it
#define LOW_BATT_MV 3400
#define SELFTEST_SECS 10
#define SELFTEST_TOL 0.10 // accepted |samples per edge - expected|

// Sync department
#define LED_MIN_MS 30
#define LED_MAX_MS 250
#define FLICKER_HZ_DEFAULT 2000

//log format
#define LOG_FORMAT_VERSION 2

// Default Sensor masks (bit i = U(i+1))
#define MASK_ALL 0x1FFF
#define MASK_STAGE1 ((1u << 12) | (1u << 0) | (1u << 3) | (1u << 6) | (1u << 9))  // U13+U1,U4,U7,U10