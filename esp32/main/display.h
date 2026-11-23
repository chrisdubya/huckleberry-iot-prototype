/* Ribbon display: 256x64 frames for the SSD1322, ported from
 * src/huckdeck/feedback/display.py. Device-independent: display_frame()
 * renders into a gfx_t; ssd1322.c pushes it to the panel and the host
 * preview writes it to an image.
 *
 * Thread-safety: the deck state is written from the dispatcher and poll
 * tasks and read by the display task; callers go through hd_lock()/hd_unlock()
 * (platform.h), which display.c takes around every entry point.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "events.h"
#include "gfx.h"

#define DISPLAY_WIDTH GFX_WIDTH
#define DISPLAY_HEIGHT GFX_HEIGHT

typedef enum { DISPLAY_MODE_TICKER, DISPLAY_MODE_PANES } display_mode_t;

/* What the screens are drawn from (display.DeckState). Times are epoch seconds. */
typedef struct {
    bool nursing_active;
    bool nursing_paused;
    char nursing_side[8];        /* "left" / "right" / "" */
    double nursing_banked;       /* seconds fed before the current segment */
    bool has_nursing_segment;
    double nursing_segment_start;
    bool has_sleep_start;
    double sleep_start;
    bool has_last_feed;
    double last_feed_start;
    char last_feed_kind[8];      /* "nursing" / "bottle" */
    char last_feed_detail[24];   /* "12m L", "120ml" */
    bool has_last_diaper;
    double last_diaper_start;
    char last_diaper_mode[8];    /* pee / poo / both / dry */
    bool has_totals;
    int diapers_today;
    int nursing_count_today;
    double nursing_seconds_today;
} deck_state_t;

/* Parsed feed/{child} document, the fields the display and dispatcher use. */
typedef struct {
    bool timer_active, timer_paused;
    bool has_feed_start, has_timer_start;
    double feed_start, timer_start;      /* as stored (seconds, or ms from the iOS app) */
    double left_duration, right_duration;
    char active_side[8], last_side[8];   /* "" when absent */
    bool has_last_nursing;
    double last_nursing_start, last_nursing_duration;
    bool has_last_bottle;
    double last_bottle_start, last_bottle_amount;
    char last_bottle_units[4];
    char prefs_last_side[8];             /* prefs.lastSide.lastSide, "" when absent */
} feed_doc_t;

typedef struct {
    bool timer_active;
    bool has_timer_start;
    double timer_start_ms;
} sleep_doc_t;

typedef struct {
    bool has_last;
    double last_start;
    char last_mode[8];
} diaper_doc_t;

typedef struct {
    display_mode_t mode;
    int brightness;        /* 0-255 after a press */
    int dim_brightness;    /* 0-255 the rest of the time */
    double dim_after;      /* seconds */
    double ticker_speed;   /* px/s */
} display_config_t;

void display_init(const display_config_t *config, double now);

/* Render the frame for `now` (epoch seconds) into g (256x64). */
void display_frame(gfx_t *g, double now);
int display_current_brightness(double now);
void display_touch(double now);

/* Same contract as the LED: dispatcher status callback. */
void display_on_event(status_t status, action_t action, const char *detail, double now);
void display_on_sleep_doc(const sleep_doc_t *doc);
void display_on_feed_doc(const feed_doc_t *doc);
void display_on_diaper_doc(const diaper_doc_t *doc);
void display_set_totals(int diapers, int nursing_count, double nursing_seconds);

/* Direct state access for previews/tests (no locking). */
deck_state_t *display_state(void);

/* Formatting helpers (exposed for the host tests). */
void format_ago(double seconds, char *out, int n);
void format_timer(double seconds, char *out, int n);
void format_hours(double seconds, char *out, int n);
double epoch_seconds(double value);
bool is_millis(double value);
