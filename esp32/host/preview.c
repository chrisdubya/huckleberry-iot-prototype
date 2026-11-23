/* Render every display screen with made-up data to a PGM contact sheet —
 * the ESP32 counterpart of src/huckdeck/dev_display.py (same scenes, same
 * NOW), so the two renderers can be compared side by side.
 *
 *   ./preview out.pgm
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "display.h"

#define NOW 1800000000.0


typedef struct {
    const char *caption;
    gfx_t frame;
    uint8_t px[GFX_WIDTH * GFX_HEIGHT];
} scene_t;

static scene_t scenes[32];
static int nscenes;

static const display_config_t CONFIG = {DISPLAY_MODE_TICKER, 80, 20, 60, 34};

static void reset(void)
{
    display_init(&CONFIG, NOW);
}

static void state_idle(void)
{
    deck_state_t *s = display_state();
    s->has_last_feed = true;
    s->last_feed_start = NOW - 134 * 60;
    strcpy(s->last_feed_kind, "nursing");
    strcpy(s->last_feed_detail, "12m L");
    s->has_last_diaper = true;
    s->last_diaper_start = NOW - 48 * 60;
    strcpy(s->last_diaper_mode, "pee");
    s->has_totals = true;
    s->diapers_today = 6;
    s->nursing_count_today = 8;
    s->nursing_seconds_today = 2 * 3600 + 35 * 60;
}

static void state_bottle(void)
{
    deck_state_t *s = display_state();
    s->has_last_feed = true;
    s->last_feed_start = NOW - 25 * 60;
    strcpy(s->last_feed_kind, "bottle");
    strcpy(s->last_feed_detail, "120ml");
    s->has_last_diaper = true;
    s->last_diaper_start = NOW - 3 * 3600 - 5 * 60;
    strcpy(s->last_diaper_mode, "both");
}

static void state_nursing(void)
{
    deck_state_t *s = display_state();
    s->nursing_active = true;
    strcpy(s->nursing_side, "left");
    s->nursing_banked = 200;
    s->has_nursing_segment = true;
    s->nursing_segment_start = NOW - 262;
    s->has_last_diaper = true;
    s->last_diaper_start = NOW - 48 * 60;
    strcpy(s->last_diaper_mode, "poo");
}

static void state_asleep(void)
{
    deck_state_t *s = display_state();
    s->has_sleep_start = true;
    s->sleep_start = NOW - 62 * 60;
    s->has_last_feed = true;
    s->last_feed_start = NOW - 75 * 60;
    strcpy(s->last_feed_kind, "nursing");
    strcpy(s->last_feed_detail, "18m R");
    s->has_last_diaper = true;
    s->last_diaper_start = NOW - 80 * 60;
    strcpy(s->last_diaper_mode, "pee");
}

static void capture(const char *caption, double now)
{
    scene_t *sc = &scenes[nscenes++];
    sc->caption = caption;
    gfx_init(&sc->frame, sc->px, GFX_WIDTH, GFX_HEIGHT);
    display_frame(&sc->frame, now);
}

int main(int argc, char **argv)
{
    const char *out = argc > 1 ? argv[1] : "display_preview.pgm";
    setenv("TZ", "EST5EDT,M3.2.0,M11.1.0", 1);

    /* ticker windows, like dev_display.ticker_scenes(): 230px steps through the strip */
    reset();
    state_idle();
    uint8_t probe_px[GFX_WIDTH * GFX_HEIGHT];
    gfx_t probe;
    gfx_init(&probe, probe_px, GFX_WIDTH, GFX_HEIGHT);
    display_frame(&probe, NOW); /* builds the strip */
    for (int offset = 0; offset < 1600 && nscenes < 8; offset += 230) {
        /* ticker_speed 34px/s: advance time so the scroll offset is `offset` */
        char *caption = malloc(48);
        snprintf(caption, 48, "ticker, %dpx in", offset);
        capture(caption, NOW + offset / 34.0);
    }
    reset(); state_nursing(); capture("nursing in progress (replaces the ticker)", NOW);
    reset(); state_asleep(); capture("asleep (replaces the ticker)", NOW);

    display_config_t panes = CONFIG;
    panes.mode = DISPLAY_MODE_PANES;
    display_init(&panes, NOW); state_idle(); capture("idle, last feed was nursing", NOW);
    display_init(&panes, NOW); state_bottle(); capture("idle, last feed was a bottle", NOW);
    display_init(&panes, NOW); state_nursing(); capture("nursing in progress", NOW);
    display_init(&panes, NOW); state_asleep(); capture("asleep", NOW);
    display_init(&panes, NOW); capture("nothing logged yet", NOW);
    display_init(&panes, NOW); state_idle(); display_on_event(ST_SENDING, ACT_POO, "", NOW); capture("button pressed", NOW);
    display_init(&panes, NOW); state_idle(); display_on_event(ST_SUCCESS, ACT_POO, "", NOW); capture("logged", NOW);
    display_init(&panes, NOW); state_idle(); display_on_event(ST_SUCCESS, ACT_NURSING_START, "", NOW); capture("nursing started", NOW);
    display_init(&panes, NOW); state_idle(); display_on_event(ST_RETRYING, ACT_BOTTLE, "", NOW); capture("retrying", NOW);
    display_init(&panes, NOW); state_idle(); display_on_event(ST_FAILED, ACT_BOTTLE, "", NOW); capture("lost", NOW);
    display_init(&panes, NOW); state_idle(); display_on_event(ST_REMOTE, ACT_SLEEP_START, "", NOW); capture("started from the phone", NOW);

    /* contact sheet: 2 columns, 24px padding, 26px caption band (captions go to stdout) */
    int cols = 2, pad = 24, cap_h = 26;
    int rows = (nscenes + cols - 1) / cols;
    int cell_w = GFX_WIDTH + pad, cell_h = GFX_HEIGHT + cap_h + pad;
    int W = cols * cell_w + pad, H = rows * cell_h + pad;
    uint8_t *img = calloc((size_t)W * H, 1);
    memset(img, 27, (size_t)W * H);
    for (int i = 0; i < nscenes; i++) {
        int x = pad + (i % cols) * cell_w, y = pad + (i / cols) * cell_h + cap_h;
        printf("%2d: %s\n", i, scenes[i].caption);
        for (int r = 0; r < GFX_HEIGHT; r++)
            memcpy(img + (size_t)(y + r) * W + x, scenes[i].px + r * GFX_WIDTH, GFX_WIDTH);
    }
    FILE *f = fopen(out, "wb");
    if (!f) {
        perror(out);
        return 1;
    }
    fprintf(f, "P5\n%d %d\n255\n", W, H);
    fwrite(img, 1, (size_t)W * H, f);
    fclose(f);
    fprintf(stderr, "Wrote %s\n", out);
    return 0;
}
