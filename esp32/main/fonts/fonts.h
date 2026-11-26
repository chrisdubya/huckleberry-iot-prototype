/* Pre-rendered glyph tables; see esp32/tools/gen_fonts.py. */
#pragma once

#include <stdint.h>

typedef struct {
    uint32_t cp;        /* Unicode code point */
    int16_t adv_q6;     /* horizontal advance in 1/64 px (Pillow's getlength) */
    uint8_t w, h;       /* bitmap size in px */
    int8_t left;        /* bitmap x offset from the pen position */
    int16_t top;        /* bitmap y offset from the baseline (negative = above) */
    uint32_t off;       /* byte offset into bits; rows are (w+1)/2 bytes, 4bpp, high nibble first */
} glyph_t;

typedef struct {
    uint8_t size;       /* the Pillow point size this was rendered at */
    int16_t ascent, descent;
    uint16_t nglyphs;
    const glyph_t *glyphs;
    const uint8_t *bits;
} font_t;

extern const font_t *const text_fonts[];
extern const int text_fonts_count;
extern const font_t *const emoji_fonts[];
extern const int emoji_fonts_count;
