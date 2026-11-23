/* SSD1322 256x64 4-bit grayscale OLED over 4-wire SPI (init sequence as luma.oled's). */
#pragma once

#include <stdint.h>

typedef struct {
    int mosi, sclk, cs, dc, rst;
} ssd1322_pins_t;

int ssd1322_init(const ssd1322_pins_t *pins, int contrast);
void ssd1322_contrast(int level); /* 0-255 */
/* Push a 256x64 8-bit grayscale frame (row-major). */
void ssd1322_display(const uint8_t *gray, int w, int h);
