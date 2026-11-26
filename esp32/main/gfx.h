/* 8-bit grayscale canvas with Pillow-compatible text drawing.
 *
 * Mirrors the subset of PIL.ImageDraw the Python deck uses (text with
 * "la"/"lm"/"ls"/"mm" anchors, textlength, lines, ellipses) on top of the
 * pre-rendered glyph tables in fonts/. Everything is clipped, so text can
 * be drawn partly off-canvas (the ticker scrolls that way).
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "fonts/fonts.h"

#define GFX_WIDTH 256
#define GFX_HEIGHT 64

typedef struct {
    int w, h;
    uint8_t *px; /* w*h bytes, row-major, 0 = black */
} gfx_t;

void gfx_init(gfx_t *g, uint8_t *buffer, int w, int h);
void gfx_clear(gfx_t *g);

/* Font lookup by Pillow size (exact match; the generator covers every size used). */
const font_t *gfx_text_font(int size);
const font_t *gfx_emoji_font(int size);

/* Width of a UTF-8 string in one font (sum of advances), px. */
float gfx_font_width(const font_t *font, const char *text);
/* Width of mixed text/emoji, each run in its own font at `size` (display._text_width). */
float gfx_text_width(const char *text, int size);
/* Largest size from `sizes` (descending) whose text font fits `text` in max_width (display._fit). */
const font_t *gfx_fit(const char *text, int max_width, const int *sizes, int n);

/* Draw `text` in one font. anchor is two chars like Pillow's: h in {l,m}, v in {a,m,s}. */
void gfx_draw(gfx_t *g, float x, float y, const char *text, const font_t *font, uint8_t fill, const char *anchor);
/* Mixed text/emoji, left-aligned at x, vertically centred on cy; returns the new x (display._draw_text). */
float gfx_draw_text(gfx_t *g, float x, float cy, const char *text, int size, uint8_t fill);

void gfx_line(gfx_t *g, float x0, float y0, float x1, float y1, uint8_t fill, int width);
void gfx_ellipse(gfx_t *g, float x0, float y0, float x1, float y1, uint8_t fill);

/* Decode one UTF-8 code point; returns bytes consumed (0 at end of string). */
int gfx_utf8_next(const char *s, uint32_t *cp);
bool gfx_is_emoji(uint32_t cp);
