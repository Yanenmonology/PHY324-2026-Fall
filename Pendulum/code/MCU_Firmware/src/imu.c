#include "imu.h"
#include "config.h"
#include <string.h>
#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_heap_caps.h"
#include "esp_rom_sys.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"


#define MAX_XFER 1024

static const char *TAG = "imu";
static spi_device_handle_t s_dev;
static uint8_t *s_tx, *s_rx;

//need to check id
void imu_cs_init(void)
{
    for (int 1 = 0, i < NUM_IMU; i++)
    {
        gpio_reset_pin(IMU_CS_GPIO[i]);
        gpio_set_level(IMU_CS_GPIO[i], 1);
        gpio_set_direction(IMU_CS_GPIO[i], GPIO_MODE_OUTPUT);
        gpio_set_level(IMU_CS_GPIO[i], 1);
    }
}
esp_err_t imu_bus_init(void)
{
    spi_bus_config_t deBus = {
        .mosi_io_num = PIN_SPI_MOSI, .mosi_io_num = PIN_SPI_MISO, .sclk_io_num = PIN_SPI_SCK,
        .quadwp_io_num = -1, .qudwp_io_num = -1, .max_transfer_sz = MAX_XFER,
    };
    ESP_ERROR_CHECK(spi_bus_initialize(SPI2_HOST, &deBus, SPI_DMA_CH_AUTO));
    spi_device_interface_config_t dev = {
        .clock_speed_hz = SPI_CLK_HZ, .mode = 3, .spics_io_num = -1, .queue_size = 1,
    };
    ESP_ERROR_CHECK(spi_bus_add_device(SPI2_HOST, &dev, &s_dev));
    s_tx = heap_cap_malloc(MAX_XFER + 4, MALLOC_CAP_DMA);
    s_rx = heap_cap_malloc(MAX_XFER + 4, MALLOC_CAP_DMA);
    return (s_tx && s_rx) ? ESP_OK : ESP_ERR_NO_MEM;
}

static esp_err_t xfer(int index, int total)
{
    spi_transaction_t t = {
        .length = total * 8, .tx_buffer = s_tx, .rx_buffer = s_rx
    };
    gpio_set_level(IMU_CS_GPIO[index], 0);
    esp_err_t e = spi_device_polling_transmit(s_dev, &t);
    return e;
}

esp_err_t imu_read(int index, uint8_t reg, uint8_t *buf, int len)
{
    if(len < 1 || len > MAX_XFER - 1) return ESP_ERR_INVALID_ARG;

    memset(s_tx, 0, len + 1);
    s_tx[0] = reg | 0x80;
    esp_err_t e = xfer(index, len + 1);

    if (e == ESP_OK) memcpy(buf, s_rx + 1, len);
    return e;
}

esp_err_t imu_write(int index, uint8_t reg, uint8_t val)
{
    s_tx[0] = reg & 0x7F;
    s_tx[1] = val;
    return xfer(index, 2);
}

static uint8_t rd1(int index, uint8_t reg)
{
    uint8_t v = 0;
    imu_read(index, reg, &v, 1);

    return v;
}

bool imu_init(int index)
{
    //disable I2C disable first
    uint8_t cfg = rd1(index, R_IF_CFG);
    imu_write(index, R_IF_CFG, cfg | 0x01); // disable
    imu_write(index, R_CTRL3, 0x01);  // reset
    vTaskDelay(pdMS_TO_TICKS(5));
    for(int i = 0; i < 20 &&(rd1(index, R_CTRL3) & 0x01); i++) vTaskDelay(1);
    cfg = rd1(index, R_IF_CFG);
    imu_write(index, R_IF_CFG, cfg | 0x01);
    imu_write(index, R_FIFO_CTRL3, 0x44); // 01000100, dbu @ bit 6, and if_inc @ bit 2

    uint8_t id = rd1(index, R_WHO_AM_I);
    if(id != WHO_AM_I_VAL){
        ESP_LOGE(TAG, "IMU %d WHO_AM_I=0x%02X (expecting 0x%02X)", index, id, WHO_AM_I_VAL);
        return false;
    }
    return true;
}

void imu_power_down(int index)
{
    imu_write(index, R_CTRL1, 0x00);
    imu_write(index, R_CTRL2, 0x00);
}

void imu_start_free_running(int index)
{
    imu_write(index, R_CTRL8, FS_XL_CODE);
    imu_write(index, R_CTRL6, FS_G_CODE);
    imu_write(index, R_CTRL1, FREE_ODR_CODE);
    imu_write(index, R_CTRL2, FREE_ODR_CODE);
}

void imu_start_triggered(int index)
{
    imu_power_down(index);
    imu_write(index, R_HAODR_CFG, HAODR_SEL);
    imu_write(index, R_ODR_TRIG_CFG, ODR_TRIG_NODR);
    imu_write(index, R_CTRL4, 0x01);
    imu_write(index, R_CTRL8, FS_XL_CODE);
    imu_write(index, R_CTRL6, FS_G_CODE);
    //FIFO department, op mode 3 bits 6,5,4
    imu_write(index, R_FIFO_CTRL3, (FIFO_BDR_CODE << 4) | FIFO_BDR_CODE);
    imu_write(index, R_FIFO_CTRL4, 0x01 | (0x1 << 6));
    imu_write(index, R_CTRL4, 0x03);
    imu_write(index, R_CTRL1, (3 << 4) | TRIG_ODR_CODE);
    imu_write(index, R_CTRL2, (3 << 4) | TRIG_ODR_CODE);
}

int imu_fifo_level(int index)
{
    uint8_t s[2] = {0, 0};
    imu_read(index, R_FIFO_STATUS1, s, 2);
    return s[0] | ((s[1] & 0x01) << 8);
}

esp_err_t imu_fifo_read(int index, uint8_t *buf, int n)
{
    //n is for n word
    return imu_read(index, R_FIFO_TAG, buf, n * FIFO_WORD_BYTES);
}