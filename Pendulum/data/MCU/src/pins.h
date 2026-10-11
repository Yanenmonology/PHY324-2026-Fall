//For GPIO map
#pragma once
#define NUM_IMU 13 // IMU[0]=U1 and IMU[12]=U13
static const int IMU_CS_GPIO[NUM_IMU] = {36, 2, 1, 4, 5, 6, 7, 15, 16, 10, 14, 35, 12};

#define PIN_SPI_SCK   18
#define PIN_SPI_MOSI  17
#define PIN_SPI_MISO  13
#define PIN_IMU_SYNC  48              // to every IMU INT2 (ODR-trigger reference)
#define PIN_IMU_INT   11              // U13 INT1 only

#define PIN_SD_CLK    39
#define PIN_SD_CMD    40
#define PIN_SD_D0     38
#define PIN_SD_D1     37
#define PIN_SD_D2     42
#define PIN_SD_D3     41

#define PIN_LED_GATE  47              // P-FET gate for 4 sync LEDs, ACTIVE LOW
#define PIN_STATUS    21
#define ADC_CH_VBAT   ADC_CHANNEL_7   // GPIO8, VBAT/2
#define ADC_CH_VBUS   ADC_CHANNEL_8   // GPIO9, VBUS/2