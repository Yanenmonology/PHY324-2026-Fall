//Add choosing the stage over wifi server on ESP 32, this is for button
#pragma once
#include <stdint.h>
#include <stdbool.h>
#include "esp_err.h"

#define BRD_NUM_IMU 13
#define STAGE_MIN 1
#define STAGE_MAX 3
#define IMU_BIT(n) ((uint16_t)(1u << ((n) - 1))) // IMU 1 to 13
#define IMU_ALL ((uint16_t) ((1u << BRD_NUM_IMU) - 1))
typedef struct stage_cfg_t
{
    uint8_t    id;               //stages
    const char *name;            //labels
    uint16_t   imu_musk;         //imu index selector
    uint8_t    sync_hz;          //IMU_SYNC square wave on GPIO 48
    uint8_t    samples_per_sync; //odr-triggered
    uint8_t    accel_fs_g;       //2,4,6,8
    uint16_t   gyro_fs_dps;      //125 to 4000
};
esp_err_t stage_init(void);
const stage_cfg_t *stage_lookup(uint8_t id);
const stage_cfg_t *stage_selected(void);
const stage_cfg_t *stage_run(void);
esp_err_t stage_request(uint8_t id);
static inline uint32_t stage_output_hz(const stage_cfg_t *c)
{
    return (uint32_t)c->sync_hz * c->samples_per_sync;
}