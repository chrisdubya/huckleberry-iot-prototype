#include "gfx.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

void gfx_init(gfx_t *g, uint8_t *buffer, int w, int h)
{
    g->w = w;
    g->h = h;
    g->px = buffer;
    gfx_clear(g);
}

void gfx_clear(gfx_t *g)
{
    memset(g->px, 0, (size_t)g->w * g->h);
}

const font_t *gfx_text_font(int size)
{
    for (int i = 0; i < text_fonts_count; i++)
        if (text_fonts[i]->size == size)
            return text_fonts[i];
    /* Not generated: fall back to the nearest smaller size rather than crash. */
    const font_t *best = text_fonts[0];
    for (int i = 0; i < text_fonts_count; i++)
        if (text_fonts[i]->size <= size && text_fonts[i]->size > best->size)
            best = text_fonts[i];
    return best;
}

const font_t *gfx_emoji_font(int size)
{
    for (int i = 0; i < emoji_fonts_count; i++)
        if (emoji_fonts[i]->size == size)
            return emoji_fonts[i];
    const font_t *best = emoji_fonts[0];
    for (int i = 0; i < emoji_fonts_count; i++)
        if (emoji_fonts[i]->size <= size && emoji_fonts[i]->size > best->size)
            best = emoji_fonts[i];
    return best;
}

int gfx_utf8_next(const char *s, uint32_t *cp)
{
    const unsigned char *p = (const unsigned char *)s;
    if (p[0] == 0)
        return 0;
    if (p[0] < 0x80) {
        *cp = p[0];
        return 1;
    }
    if ((p[0] & 0xE0) == 0xC0 && p[1]) {
        *cp = ((p[0] & 0x1F) << 6) | (p[1] & 0x3F);
        return 2;
    }
    if ((p[0] & 0xF0) == 0xE0 && p[1] && p[2]) {
        *cp = ((p[0] & 0x0F) << 12) | ((p[1] & 0x3F) << 6) | (p[2] & 0x3F);
        return 3;
    }
    if ((p[0] & 0xF8) == 0xF0 && p[1] && p[2] && p[3]) {
        *cp = ((p[0] & 0x07) << 18) | ((p[1] & 0x3F) << 12) | ((p[2] & 0x3F) << 6) | (p[3] & 0x3F);
        return 4;
    }
    *cp = '?';
    return 1;
}

bool gfx_is_emoji(uint32_t cp)
{
    return cp >= 0x1F000 || cp == 0xFE0F; /* display._is_emoji */
}

static const glyph_t *find_glyph(const font_t *font, uint32_t cp)
{
    for (int i = 0; i < font->nglyphs; i++)
        if (font->glyphs[i].cp == cp)
            return &font->glyphs[i];
    if (cp != '?' && cp < 0x1F000)
        return find_glyph(font, '?');
    return NULL;
}

float gfx_font_width(const font_t *font, const char *text)
{
    float w = 0;
    uint32_t cp;
    int n;
    while ((n = gfx_utf8_next(text, &cp)) > 0) {
        text += n;
        if (cp == 0xFE0F)
            continue;
        const glyph_t *gl = find_glyph(font, cp);
        if (gl)
            w += gl->adv_q6 / 64.0f;
    }
    return w;
}

/* Walk `text` as (run, is_emoji) chunks like display._segments; calls fn per run. */
typedef void (*run_fn)(void *ctx, const char *run, int len, bool emoji);

static void for_each_run(const char *text, run_fn fn, void *ctx)
{
    const char *start = text;
    int start_len = 0;
    bool cur_emoji = false;
    uint32_t cp;
    int n;
    const char *p = text;
    while ((n = gfx_utf8_next(p, &cp)) > 0) {
        if (cp == 0xFE0F) {
            /* variation selector: skipped, which also ends the current run cleanly */
            if (start_len)
                fn(ctx, start, start_len, cur_emoji);
            start_len = 0;
            p += n;
            start = p;
            continue;
        }
        bool emoji = gfx_is_emoji(cp);
        if (start_len && emoji != cur_emoji) {
            fn(ctx, start, start_len, cur_emoji);
            start = p;
            start_len = 0;
        }
        cur_emoji = emoji;
        start_len += n;
        p += n;
    }
    if (start_len)
        fn(ctx, start, start_len, cur_emoji);
}

typedef struct {
    int size;
    float width;
} width_ctx;

static void width_run(void *vctx, const char *run, int len, bool emoji)
{
    width_ctx *ctx = vctx;
    char buf[128];
    if (len >= (int)sizeof(buf))
        len = sizeof(buf) - 1;
    memcpy(buf, run, len);
    buf[len] = 0;
    ctx->width += gfx_font_width(emoji ? gfx_emoji_font(ctx->size) : gfx_text_font(ctx->size), buf);
}

float gfx_text_width(const char *text, int size)
{
    width_ctx ctx = {size, 0};
    for_each_run(text, width_run, &ctx);
    return ctx.width;
}

const font_t *gfx_fit(const char *text, int max_width, const int *sizes, int n)
{
    for (int i = 0; i < n; i++) {
        const font_t *font = gfx_text_font(sizes[i]);
        if (gfx_font_width(font, text) <= max_width)
            return font;
    }
    return gfx_text_font(sizes[n - 1]);
}

static inline void blend(gfx_t *g, int x, int y, uint8_t fill, uint8_t alpha)
{
    if (x < 0 || y < 0 || x >= g->w || y >= g->h || alpha == 0)
        return;
    uint8_t *p = &g->px[y * g->w + x];
    if (alpha == 255) {
        *p = fill;
        return;
    }
    *p = (uint8_t)((*p * (255 - alpha) + fill * alpha) / 255);
}

static void draw_glyph(gfx_t *g, const font_t *font, const glyph_t *gl, int pen_x, int baseline_y, uint8_t fill)
{
    int stride = (gl->w + 1) / 2;
    const uint8_t *bits = font->bits + gl->off;
    int x0 = pen_x + gl->left;
    int y0 = baseline_y + gl->top;
    for (int y = 0; y < gl->h; y++) {
        int py = y0 + y;
        if (py < 0 || py >= g->h)
            continue;
        const uint8_t *row = bits + y * stride;
        for (int x = 0; x < gl->w; x++) {
            uint8_t nib = (x & 1) ? (row[x >> 1] & 0x0F) : (row[x >> 1] >> 4);
            if (nib)
                blend(g, x0 + x, py, fill, (uint8_t)(nib * 17));
        }
    }
}

void gfx_draw(gfx_t *g, float x, float y, const char *text, const font_t *font, uint8_t fill, const char *anchor)
{
    char h = anchor ? anchor[0] : 'l';
    char v = anchor ? anchor[1] : 'a';
    if (h == 'm')
        x -= gfx_font_width(font, text) / 2;
    float baseline;
    switch (v) {
    case 's': baseline = y; break;
    case 'm': baseline = y + (font->ascent - font->descent) / 2.0f; break;
    default: baseline = y + font->ascent; break; /* 'a' */
    }
    float pen = x;
    int by = (int)lroundf(baseline);
    uint32_t cp;
    int n;
    while ((n = gfx_utf8_next(text, &cp)) > 0) {
        text += n;
        if (cp == 0xFE0F)
            continue;
        const glyph_t *gl = find_glyph(font, cp);
        if (!gl)
            continue;
        draw_glyph(g, font, gl, (int)lroundf(pen), by, fill);
        pen += gl->adv_q6 / 64.0f;
    }
}

typedef struct {
    gfx_t *g;
    float x, cy;
    int size;
    uint8_t fill;
} draw_ctx;

static void draw_run(void *vctx, const char *run, int len, bool emoji)
{
    draw_ctx *ctx = vctx;
    char buf[128];
    if (len >= (int)sizeof(buf))
        len = sizeof(buf) - 1;
    memcpy(buf, run, len);
    buf[len] = 0;
    const font_t *font = emoji ? gfx_emoji_font(ctx->size) : gfx_text_font(ctx->size);
    gfx_draw(ctx->g, ctx->x, ctx->cy, buf, font, ctx->fill, "lm");
    ctx->x += gfx_font_width(font, buf);
}

float gfx_draw_text(gfx_t *g, float x, float cy, const char *text, int size, uint8_t fill)
{
    draw_ctx ctx = {g, x, cy, size, fill};
    for_each_run(text, draw_run, &ctx);
    return ctx.x;
}

static void fill_disc(gfx_t *g, float cx, float cy, float r, uint8_t fill)
{
    int x0 = (int)floorf(cx - r), x1 = (int)ceilf(cx + r);
    int y0 = (int)floorf(cy - r), y1 = (int)ceilf(cy + r);
    for (int y = y0; y <= y1; y++)
        for (int x = x0; x <= x1; x++) {
            float dx = x + 0.5f - cx, dy = y + 0.5f - cy;
            if (dx * dx + dy * dy <= r * r)
                blend(g, x, y, fill, 255);
        }
}

void gfx_line(gfx_t *g, float x0, float y0, float x1, float y1, uint8_t fill, int width)
{
    float dx = x1 - x0, dy = y1 - y0;
    float len = sqrtf(dx * dx + dy * dy);
    if (width <= 1) {
        int steps = (int)ceilf(len);
        if (steps < 1)
            steps = 1;
        for (int i = 0; i <= steps; i++) {
            float t = (float)i / steps;
            blend(g, (int)lroundf(x0 + dx * t), (int)lroundf(y0 + dy * t), fill, 255);
        }
        return;
    }
    /* Thick line: stamp discs along it (round joints, like Pillow's joint="curve"). */
    float r = width / 2.0f;
    int steps = (int)ceilf(len * 2);
    if (steps < 1)
        steps = 1;
    for (int i = 0; i <= steps; i++) {
        float t = (float)i / steps;
        fill_disc(g, x0 + dx * t + 0.5f, y0 + dy * t + 0.5f, r, fill);
    }
}

void gfx_ellipse(gfx_t *g, float x0, float y0, float x1, float y1, uint8_t fill)
{
    float cx = (x0 + x1) / 2 + 0.5f, cy = (y0 + y1) / 2 + 0.5f;
    float rx = (x1 - x0) / 2 + 0.5f, ry = (y1 - y0) / 2 + 0.5f;
    for (int y = (int)floorf(y0); y <= (int)ceilf(y1); y++)
        for (int x = (int)floorf(x0); x <= (int)ceilf(x1); x++) {
            float dx = (x + 0.5f - cx) / rx, dy = (y + 0.5f - cy) / ry;
            if (dx * dx + dy * dy <= 1.0f)
                blend(g, x, y, fill, 255);
        }
}
