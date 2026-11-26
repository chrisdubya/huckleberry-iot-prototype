#include "ssd1322.h"

#include <string.h>

#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "ssd1322";

#define PANEL_W 256
#define PANEL_H 64
#define COLUMN_OFFSET ((480 - PANEL_W) / 2) /* luma: the RAM is 480 columns wide */

static spi_device_handle_t dev;
static int dc_pin, rst_pin;
static uint8_t *dma_buf; /* PANEL_W * PANEL_H / 2 */

static void send(const uint8_t *data, int len, int is_data)
{
    gpio_set_level(dc_pin, is_data);
    spi_transaction_t t = {
        .length = (size_t)len * 8,
        .tx_buffer = data,
    };
    spi_device_polling_transmit(dev, &t);
}

static void command(uint8_t cmd, const uint8_t *args, int nargs)
{
    send(&cmd, 1, 0);
    if (nargs)
        send(args, nargs, 1); /* arguments go out as data, like luma does */
}

#define CMD(c, ...) do { const uint8_t a_[] = {__VA_ARGS__}; command(c, a_, sizeof(a_)); } while (0)
#define CMD0(c) command(c, NULL, 0)

static void set_window(void)
{
    uint8_t cols[] = {COLUMN_OFFSET >> 2, ((COLUMN_OFFSET + PANEL_W) >> 2) - 1};
    uint8_t rows[] = {0, PANEL_H - 1};
    command(0x15, cols, 2); /* column address */
    command(0x75, rows, 2); /* row address */
    CMD0(0x5C);             /* write RAM */
}

int ssd1322_init(const ssd1322_pins_t *pins, int contrast)
{
    dc_pin = pins->dc;
    rst_pin = pins->rst;
    gpio_config_t out = {
        .pin_bit_mask = (1ULL << pins->dc) | (1ULL << pins->rst),
        .mode = GPIO_MODE_OUTPUT,
    };
    gpio_config(&out);

    spi_bus_config_t bus = {
        .mosi_io_num = pins->mosi,
        .miso_io_num = -1,
        .sclk_io_num = pins->sclk,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = PANEL_W * PANEL_H / 2,
    };
    esp_err_t err = spi_bus_initialize(SPI2_HOST, &bus, SPI_DMA_CH_AUTO);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "spi_bus_initialize: %s", esp_err_to_name(err));
        return -1;
    }
    spi_device_interface_config_t devcfg = {
        .clock_speed_hz = 10 * 1000 * 1000,
        .mode = 0,
        .spics_io_num = pins->cs,
        .queue_size = 1,
    };
    err = spi_bus_add_device(SPI2_HOST, &devcfg, &dev);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "spi_bus_add_device: %s", esp_err_to_name(err));
        return -1;
    }
    dma_buf = heap_caps_malloc(PANEL_W * PANEL_H / 2, MALLOC_CAP_DMA);
    if (!dma_buf)
        return -1;

    gpio_set_level(rst_pin, 0);
    vTaskDelay(pdMS_TO_TICKS(10));
    gpio_set_level(rst_pin, 1);
    vTaskDelay(pdMS_TO_TICKS(10));

    CMD(0xFD, 0x12);       /* unlock IC */
    CMD0(0xA4);            /* display off (all pixels off) */
    CMD(0xB3, 0xF2);       /* clock divide ratio / frequency */
    CMD(0xCA, 0x3F);       /* MUX ratio */
    CMD(0xA2, 0x00);       /* display offset */
    CMD(0xA1, 0x00);       /* display start line */
    CMD(0xA0, 0x14, 0x11); /* remap & dual COM line */
    CMD(0xB5, 0x00);       /* GPIO disabled */
    CMD(0xAB, 0x01);       /* function select (internal Vdd) */
    CMD(0xB4, 0xA0, 0xFD); /* display enhancement A (external VSL) */
    CMD(0xC7, 0x0F);       /* master contrast */
    CMD0(0xB9);            /* default grayscale table */
    CMD(0xB1, 0xF0);       /* phase length */
    CMD(0xD1, 0x82, 0x20); /* display enhancement B */
    CMD(0xBB, 0x0D);       /* pre-charge voltage */
    CMD(0xB6, 0x08);       /* 2nd precharge period */
    CMD(0xBE, 0x00);       /* VcomH */
    CMD0(0xA6);            /* normal display */
    CMD0(0xA9);            /* exit partial display */
    ssd1322_contrast(contrast);
    memset(dma_buf, 0, PANEL_W * PANEL_H / 2);
    set_window();
    send(dma_buf, PANEL_W * PANEL_H / 2, 1);
    CMD0(0xAF); /* display on */
    return 0;
}

void ssd1322_contrast(int level)
{
    uint8_t v = level < 0 ? 0 : level > 255 ? 255 : level;
    command(0xC1, &v, 1);
}

void ssd1322_display(const uint8_t *gray, int w, int h)
{
    if (!dma_buf || w != PANEL_W || h != PANEL_H)
        return;
    const uint8_t *p = gray;
    uint8_t *out = dma_buf;
    for (int i = 0; i < w * h; i += 2) {
        *out++ = (p[i] & 0xF0) | (p[i + 1] >> 4); /* two 4-bit pixels per byte, first in the high nibble */
    }
    set_window();
    send(dma_buf, PANEL_W * PANEL_H / 2, 1);
}
