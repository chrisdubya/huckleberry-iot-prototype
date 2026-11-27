#include "display.h"

#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

#include "platform.h"

#define BRIGHT 255
#define DIM 120 /* labels and separators; the panel has 4-bit grayscale */
#define FAINT 50

#define SUCCESS_SECONDS 3.0
#define FAILED_SECONDS 5.0
#define REMOTE_SECONDS 3.0

#define TICKER_FONT 40   /* one line, readable from across the room */
#define SESSION_FONT 44  /* the live timer while a session runs */
#define SESSION_DRIFT_SECONDS 20
#define SESSION_HOLD_SECONDS 20.0  /* the timer holds this long, then the other info comes by */
#define SESSION_PANES_SECONDS 10.0 /* panes mode: how long the panes show between timers */
#define TICKER_GAP_PX 44 /* between items, with a dot in the middle */
#define TICKER_DOT_R 3

static const int SESSION_DRIFT[] = {0, 1, 2, 1, 0, -1, -2, -1};

static const char *DIAPER_EMOJI_PEE = "\xF0\x9F\x92\xA7";          /* 💧 */
static const char *DIAPER_EMOJI_POO = "\xF0\x9F\x92\xA9";          /* 💩 */
static const char *DIAPER_EMOJI_BOTH = "\xF0\x9F\x92\xA7\xF0\x9F\x92\xA9";
static const char *NURSING_EMOJI = "\xF0\x9F\xA4\xB1";             /* 🤱 */
static const char *SLEEP_EMOJI = "\xF0\x9F\x98\xB4";               /* 😴 */
static const char *BOTTLE_EMOJI = "\xF0\x9F\x8D\xBC";              /* 🍼 */

typedef struct {
    bool active;
    char title[40];
    char sub[40];
    char mark[8]; /* "check" / "cross" / "" */
    bool has_until;
    double until;
} overlay_t;

static struct {
    display_config_t config;
    deck_state_t state;
    overlay_t overlay;
    double last_input;
    /* ticker scroll bookkeeping (display.Display._ticker_frame) */
    char strip_key[256];
    int strip_width;
    double scroll_start;
    double scroll_offset; /* px into the strip at scroll_start */
    /* when the session timer last took the panel (display.Display._session_frame) */
    bool has_session_cycle;
    double session_cycle_start;
} D;

/* -- helpers ------------------------------------------------------------ */

static const char *diaper_label(const char *mode)
{
    if (!strcmp(mode, "pee")) return "PEE";
    if (!strcmp(mode, "poo")) return "POOP";
    if (!strcmp(mode, "both")) return "PEE+POOP";
    if (!strcmp(mode, "dry")) return "DRY";
    return "DIAPER";
}

static const char *diaper_emoji(const char *mode)
{
    if (!strcmp(mode, "pee")) return DIAPER_EMOJI_PEE;
    if (!strcmp(mode, "poo")) return DIAPER_EMOJI_POO;
    if (!strcmp(mode, "both")) return DIAPER_EMOJI_BOTH;
    if (!strcmp(mode, "dry")) return "DRY";
    return "DIAPER";
}

static long floordiv(double value, long divisor)
{
    return (long)floor(value / divisor);
}

void format_ago(double seconds, char *out, int n)
{
    long minutes = floordiv(seconds > 0 ? seconds : 0, 60);
    if (minutes < 1) {
        snprintf(out, n, "now");
        return;
    }
    if (minutes < 60) {
        snprintf(out, n, "%ldm", minutes);
        return;
    }
    long hours = minutes / 60;
    minutes %= 60;
    if (hours < 24)
        snprintf(out, n, "%ldh %02ldm", hours, minutes);
    else
        snprintf(out, n, "%ldd %ldh", hours / 24, hours % 24);
}

void format_timer(double seconds, char *out, int n)
{
    long s = (long)(seconds > 0 ? seconds : 0);
    if (s < 3600)
        snprintf(out, n, "%02ld:%02ld", s / 60, s % 60);
    else
        snprintf(out, n, "%ldh %02ldm", s / 3600, s % 3600 / 60);
}

static void format_duration(double seconds, char *out, int n)
{
    long minutes = lrint(seconds / 60); /* Python's round(): half to even */
    if (minutes < 1)
        minutes = 1;
    snprintf(out, n, "%ldm", minutes);
}

void format_hours(double seconds, char *out, int n)
{
    long minutes = floordiv(seconds > 0 ? seconds : 0, 60);
    if (minutes < 60)
        snprintf(out, n, "%ldm", minutes);
    else
        snprintf(out, n, "%ldh %02ldm", minutes / 60, minutes % 60);
}

/* Seconds-since-epoch values stay below this for ~3000 more years; millisecond
 * values passed it in 1973 (src/huckdeck/epoch.py). */
#define MS_THRESHOLD 1e11

bool is_millis(double value)
{
    return value > MS_THRESHOLD;
}

double epoch_seconds(double value)
{
    return value > MS_THRESHOLD ? value / 1000 : value;
}

static void clock_string(double now, char *out, int n)
{
    time_t t = (time_t)now;
    struct tm tm;
    localtime_r(&t, &tm);
    char buf[16];
    strftime(buf, sizeof(buf), "%I:%M %p", &tm);
    const char *p = buf;
    while (*p == '0')
        p++;
    snprintf(out, n, "%s", p);
}

static void upper(char *s)
{
    for (; *s; s++)
        if (*s >= 'a' && *s <= 'z')
            *s -= 32;
}

/* Weekday, month and day, e.g. "FRI NOV 27". */
static void date_string(double now, char *out, int n)
{
    time_t t = (time_t)now;
    struct tm tm;
    localtime_r(&t, &tm);
    char buf[16];
    strftime(buf, sizeof(buf), "%a %b", &tm);
    upper(buf);
    snprintf(out, n, "%s %d", buf, tm.tm_mday);
}

/* -- panes layout ------------------------------------------------------- */

typedef struct {
    char label[32];
    char value[16];
    char suffix[8];
} pane_t;

static double nursing_elapsed(const deck_state_t *s, double now)
{
    double elapsed = s->nursing_banked;
    if (!s->nursing_paused && s->has_nursing_segment)
        elapsed += now - s->nursing_segment_start;
    return elapsed;
}

static int build_panes(const deck_state_t *s, double now, pane_t *panes)
{
    int n = 0;
    char ago[16];
    if (s->nursing_active) {
        pane_t *p = &panes[n++];
        snprintf(p->label, sizeof(p->label), "%s", s->nursing_paused ? "PAUSED" : "NURSING");
        if (s->nursing_side[0]) {
            char side[8];
            snprintf(side, sizeof(side), "%s", s->nursing_side);
            upper(side);
            size_t len = strlen(p->label);
            snprintf(p->label + len, sizeof(p->label) - len, " %s", side);
        }
        format_timer(nursing_elapsed(s, now), p->value, sizeof(p->value));
        p->suffix[0] = 0;
    } else if (s->has_last_feed) {
        pane_t *p = &panes[n++];
        format_ago(now - s->last_feed_start, ago, sizeof(ago));
        snprintf(p->label, sizeof(p->label), "%s%s%s", !strcmp(s->last_feed_kind, "bottle") ? "BOTTLE" : "NURSED",
                 s->last_feed_detail[0] ? " " : "", s->last_feed_detail);
        snprintf(p->value, sizeof(p->value), "%s", ago);
        snprintf(p->suffix, sizeof(p->suffix), "%s", strcmp(ago, "now") ? "ago" : "");
    } else {
        pane_t *p = &panes[n++];
        snprintf(p->label, sizeof(p->label), "FED");
        snprintf(p->value, sizeof(p->value), "--");
        p->suffix[0] = 0;
    }

    if (s->has_sleep_start) {
        pane_t *p = &panes[n++];
        snprintf(p->label, sizeof(p->label), "ASLEEP");
        format_timer(now - s->sleep_start, p->value, sizeof(p->value));
        p->suffix[0] = 0;
    }

    if (s->has_last_diaper) {
        pane_t *p = &panes[n++];
        format_ago(now - s->last_diaper_start, ago, sizeof(ago));
        const char *label = diaper_label(s->last_diaper_mode);
        if (!strcmp(label, "DIAPER"))
            snprintf(p->label, sizeof(p->label), "DIAPER");
        else
            snprintf(p->label, sizeof(p->label), "DIAPER %s", label);
        snprintf(p->value, sizeof(p->value), "%s", ago);
        snprintf(p->suffix, sizeof(p->suffix), "%s", strcmp(ago, "now") ? "ago" : "");
    } else {
        pane_t *p = &panes[n++];
        snprintf(p->label, sizeof(p->label), "DIAPER");
        snprintf(p->value, sizeof(p->value), "--");
        p->suffix[0] = 0;
    }
    return n;
}

static void draw_panes(gfx_t *g, const deck_state_t *s, double now)
{
    static const int LABEL_SIZES[] = {13, 12, 11, 10};
    static const int VALUE_SIZES[] = {34, 30, 26, 22, 18};
    pane_t panes[3];
    int n = build_panes(s, now, panes);
    int pane_w = DISPLAY_WIDTH / n;
    int pad = 6;
    for (int i = 0; i < n; i++) {
        pane_t *p = &panes[i];
        int x = i * pane_w;
        if (i)
            gfx_line(g, x, 6, x, DISPLAY_HEIGHT - 7, FAINT, 1);
        int inner = pane_w - 2 * pad;
        gfx_draw(g, x + pad, 3, p->label, gfx_fit(p->label, inner, LABEL_SIZES, 4), DIM, "la");
        const font_t *suffix_font = gfx_text_font(13);
        char suffix_text[12] = "";
        float suffix_w = 0;
        if (p->suffix[0]) {
            snprintf(suffix_text, sizeof(suffix_text), " %s", p->suffix);
            suffix_w = gfx_font_width(suffix_font, suffix_text);
        }
        if (p->suffix[0] && gfx_font_width(gfx_text_font(22), p->value) > inner - suffix_w) {
            p->suffix[0] = 0; /* narrow pane: the number matters more than "ago" */
            suffix_w = 0;
        }
        const font_t *value_font = gfx_fit(p->value, (int)(inner - suffix_w), VALUE_SIZES, 5);
        gfx_draw(g, x + pad, 58, p->value, value_font, BRIGHT, "ls");
        if (p->suffix[0]) {
            float value_w = gfx_font_width(value_font, p->value);
            gfx_draw(g, x + pad + value_w, 58, suffix_text, suffix_font, DIM, "ls");
        }
    }
}

/* -- overlay ------------------------------------------------------------ */

static int draw_mark(gfx_t *g, const char *mark, int x, int cy)
{
    if (!strcmp(mark, "check")) {
        gfx_line(g, x, cy, x + 7, cy + 8, BRIGHT, 4);
        gfx_line(g, x + 7, cy + 8, x + 20, cy - 9, BRIGHT, 4);
        return 20;
    }
    if (!strcmp(mark, "cross")) {
        gfx_line(g, x, cy - 9, x + 18, cy + 9, BRIGHT, 4);
        gfx_line(g, x, cy + 9, x + 18, cy - 9, BRIGHT, 4);
        return 18;
    }
    return 0;
}

static void draw_overlay(gfx_t *g, const overlay_t *o)
{
    static const int TITLE_SIZES[] = {30, 26, 22, 18, 15};
    static const int SUB_SIZES[] = {14, 12, 10};
    int mark_w = !strcmp(o->mark, "check") ? 20 : !strcmp(o->mark, "cross") ? 18 : 0;
    int gap = mark_w ? 10 : 0;
    int title_y = o->sub[0] ? 24 : DISPLAY_HEIGHT / 2;
    const font_t *font = gfx_fit(o->title, DISPLAY_WIDTH - 16 - mark_w - gap, TITLE_SIZES, 5);
    float title_w = gfx_font_width(font, o->title);
    int x = (int)floorf((DISPLAY_WIDTH - (mark_w + gap + title_w)) / 2);
    draw_mark(g, o->mark, x, title_y);
    gfx_draw(g, x + mark_w + gap, title_y, o->title, font, BRIGHT, "lm");
    if (o->sub[0])
        gfx_draw(g, DISPLAY_WIDTH / 2, 52, o->sub, gfx_fit(o->sub, DISPLAY_WIDTH - 16, SUB_SIZES, 3), DIM, "mm");
}

/* -- ticker ------------------------------------------------------------- */

typedef struct {
    char text[48];
    uint8_t fill;
} run_t;

typedef struct {
    run_t runs[6];
    int nruns;
} item_t;

#define TICKER_MAX_ITEMS 5

static void add_run(item_t *item, uint8_t fill, const char *fmt, ...) __attribute__((format(printf, 3, 4)));
static void add_run(item_t *item, uint8_t fill, const char *fmt, ...)
{
    if (item->nruns >= 6)
        return;
    run_t *r = &item->runs[item->nruns++];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(r->text, sizeof(r->text), fmt, ap);
    va_end(ap);
    r->fill = fill;
    if (!r->text[0])
        item->nruns--; /* Pillow draws nothing for "", skip it */
}

/* display.ticker_items: the date and time, the diaper group (latest, today's total), then the nursing group. */
static int ticker_items(const deck_state_t *s, double now, item_t *items)
{
    int n = 0;
    char ago[16], hours[16], date[24], clock[16];
    memset(items, 0, sizeof(item_t) * TICKER_MAX_ITEMS);
    date_string(now, date, sizeof(date));
    clock_string(now, clock, sizeof(clock));
    char *meridiem = strchr(clock, ' '); /* "3:42 PM": bright time, dim AM/PM */
    if (meridiem)
        *meridiem++ = 0;
    {
        item_t *it = &items[n++];
        add_run(it, DIM, "%s ", date);
        add_run(it, BRIGHT, "%s", clock);
        add_run(it, DIM, " %s", meridiem ? meridiem : "");
    }
    if (s->has_last_diaper) {
        format_ago(now - s->last_diaper_start, ago, sizeof(ago));
        item_t *it = &items[n++];
        add_run(it, DIM, "%s ", diaper_emoji(s->last_diaper_mode));
        add_run(it, BRIGHT, "%s", ago);
        add_run(it, DIM, "%s", strcmp(ago, "now") ? " ago" : "");
    } else {
        item_t *it = &items[n++];
        add_run(it, DIM, "%s ", DIAPER_EMOJI_BOTH);
        add_run(it, BRIGHT, "--");
    }
    if (s->has_totals) {
        item_t *it = &items[n++];
        add_run(it, DIM, "TODAY ");
        add_run(it, BRIGHT, "%d", s->diapers_today);
        add_run(it, DIM, " %s", DIAPER_EMOJI_BOTH);
    }
    if (s->has_last_feed) {
        format_ago(now - s->last_feed_start, ago, sizeof(ago));
        item_t *it = &items[n++];
        add_run(it, DIM, "%s %s%s", !strcmp(s->last_feed_kind, "bottle") ? BOTTLE_EMOJI : NURSING_EMOJI,
                s->last_feed_detail, s->last_feed_detail[0] ? " " : "");
        add_run(it, BRIGHT, "%s", ago);
        add_run(it, DIM, "%s", strcmp(ago, "now") ? " ago" : "");
    } else {
        item_t *it = &items[n++];
        add_run(it, DIM, "%s ", NURSING_EMOJI);
        add_run(it, BRIGHT, "--");
    }
    if (s->has_totals) {
        int count = s->nursing_count_today;
        format_hours(s->nursing_seconds_today, hours, sizeof(hours));
        item_t *it = &items[n++];
        add_run(it, DIM, "TODAY %s ", NURSING_EMOJI);
        add_run(it, BRIGHT, "%s", hours);
        add_run(it, DIM, ", ");
        add_run(it, BRIGHT, "%d", count);
        add_run(it, DIM, " FEED%s", count == 1 ? "" : "S");
    }
    return n;
}

static int strip_width(const item_t *items, int n)
{
    float total = 0;
    for (int i = 0; i < n; i++)
        for (int r = 0; r < items[i].nruns; r++)
            total += gfx_text_width(items[i].runs[r].text, TICKER_FONT);
    int width = (int)(total + TICKER_GAP_PX * n); /* a gap after every item, so the loop joins cleanly */
    return width < 1 ? 1 : width;
}

static void draw_strip_at(gfx_t *g, const item_t *items, int n, float x)
{
    for (int i = 0; i < n; i++) {
        for (int r = 0; r < items[i].nruns; r++)
            x = gfx_draw_text(g, x, DISPLAY_HEIGHT / 2.0f, items[i].runs[r].text, TICKER_FONT, items[i].runs[r].fill);
        float cx = x + TICKER_GAP_PX / 2.0f, cy = DISPLAY_HEIGHT / 2.0f + 2;
        gfx_ellipse(g, cx - TICKER_DOT_R, cy - TICKER_DOT_R, cx + TICKER_DOT_R, cy + TICKER_DOT_R, DIM);
        x += TICKER_GAP_PX;
    }
}

static double scroll_offset(double now)
{
    return fmod(D.scroll_offset + (now - D.scroll_start) * D.config.ticker_speed, D.strip_width);
}

/* Build the items for `now` and refresh the strip bookkeeping if their text changed. */
static int update_strip(double now, item_t *items)
{
    int n = ticker_items(&D.state, now, items);
    char key[256] = "";
    size_t len = 0;
    for (int i = 0; i < n && len < sizeof(key) - 1; i++)
        for (int r = 0; r < items[i].nruns && len < sizeof(key) - 1; r++)
            len += snprintf(key + len, sizeof(key) - len, "%s|%d;", items[i].runs[r].text, items[i].runs[r].fill);
    if (strcmp(key, D.strip_key)) {
        /* text changed (a minute ticked over, a session started...): keep the
         * scroll position so the motion stays smooth */
        D.scroll_offset = D.strip_width ? scroll_offset(now) : 0;
        D.scroll_start = now;
        D.strip_width = strip_width(items, n);
        snprintf(D.strip_key, sizeof(D.strip_key), "%s", key);
    }
    return n;
}

static void ticker_frame(gfx_t *g, double now)
{
    item_t items[TICKER_MAX_ITEMS];
    int n = update_strip(now, items);
    int offset = (int)scroll_offset(now);
    draw_strip_at(g, items, n, -(float)offset);
    if (offset + DISPLAY_WIDTH > D.strip_width)
        draw_strip_at(g, items, n, (float)(D.strip_width - offset));
}

/* -- session timer ------------------------------------------------------ */

typedef struct {
    char label[24];
    char value[16];
} timer_t_;

static int session_timers(const deck_state_t *s, double now, timer_t_ *timers)
{
    int n = 0;
    if (s->nursing_active) {
        timer_t_ *t = &timers[n++];
        snprintf(t->label, sizeof(t->label), "%s", NURSING_EMOJI);
        if (s->nursing_side[0]) {
            size_t len = strlen(t->label);
            snprintf(t->label + len, sizeof(t->label) - len, " %c", s->nursing_side[0] - 32);
        }
        if (s->nursing_paused) {
            size_t len = strlen(t->label);
            snprintf(t->label + len, sizeof(t->label) - len, " PAUSED");
        }
        format_timer(nursing_elapsed(s, now), t->value, sizeof(t->value));
    }
    if (s->has_sleep_start) {
        timer_t_ *t = &timers[n++];
        snprintf(t->label, sizeof(t->label), "%s", SLEEP_EMOJI);
        format_timer(now - s->sleep_start, t->value, sizeof(t->value));
    }
    return n;
}

static void render_session(gfx_t *g, const timer_t_ *timers, int n, double now)
{
    int drift = SESSION_DRIFT[(long)floor(now / SESSION_DRIFT_SECONDS) % 8];
    int slot_w = DISPLAY_WIDTH / (n > 1 ? n : 1);
    for (int i = 0; i < n; i++) {
        char label[32];
        snprintf(label, sizeof(label), "%.*s ", (int)sizeof(timers[i].label) - 1, timers[i].label);
        int size = SESSION_FONT;
        while (size > 16 && gfx_text_width(label, size) + gfx_text_width(timers[i].value, size) > slot_w - 12)
            size -= 2;
        float total = gfx_text_width(label, size) + gfx_text_width(timers[i].value, size);
        float x = i * slot_w + (slot_w - total) / 2 + drift;
        float cy = DISPLAY_HEIGHT / 2.0f + drift / 2.0f;
        x = gfx_draw_text(g, x, cy, label, size, DIM);
        gfx_draw_text(g, x, cy, timers[i].value, size, BRIGHT);
    }
}

/* A running session: the big timer, with the other info coming by between holds. */
static void session_frame(gfx_t *g, const timer_t_ *timers, int n, double now)
{
    if (!D.has_session_cycle) {
        D.has_session_cycle = true;
        D.session_cycle_start = now;
    }
    double showing = now - D.session_cycle_start - SESSION_HOLD_SECONDS; /* seconds into the other info */
    if (showing >= 0) {
        if (D.config.mode != DISPLAY_MODE_TICKER) {
            if (showing < SESSION_PANES_SECONDS) {
                draw_panes(g, &D.state, now);
                return;
            }
        } else {
            /* one pass: the strip comes in from the right edge and leaves off the left */
            item_t items[TICKER_MAX_ITEMS];
            int nitems = update_strip(now, items);
            int x = DISPLAY_WIDTH - (int)(showing * D.config.ticker_speed);
            if (x > -D.strip_width) {
                draw_strip_at(g, items, nitems, (float)x);
                return;
            }
        }
        D.session_cycle_start = now;
    }
    render_session(g, timers, n, now);
}

/* -- public ------------------------------------------------------------- */

void display_init(const display_config_t *config, double now)
{
    memset(&D, 0, sizeof(D));
    D.config = *config;
    D.last_input = now;
    D.scroll_start = now;
    D.strip_width = 1;
}

deck_state_t *display_state(void)
{
    return &D.state;
}

int display_current_brightness(double now)
{
    hd_lock();
    int level = now - D.last_input < D.config.dim_after ? D.config.brightness : D.config.dim_brightness;
    hd_unlock();
    return level;
}

void display_touch(double now)
{
    hd_lock();
    D.last_input = now;
    hd_unlock();
}

void display_frame(gfx_t *g, double now)
{
    hd_lock();
    gfx_clear(g);
    if (D.overlay.active && D.overlay.has_until && now >= D.overlay.until)
        D.overlay.active = false;
    if (D.overlay.active) {
        D.has_session_cycle = false; /* after a press, a running session's timer comes first */
        draw_overlay(g, &D.overlay);
    } else {
        timer_t_ timers[2];
        int n = session_timers(&D.state, now, timers);
        if (n) {
            session_frame(g, timers, n, now);
        } else {
            D.has_session_cycle = false;
            if (D.config.mode != DISPLAY_MODE_TICKER)
                draw_panes(g, &D.state, now);
            else
                ticker_frame(g, now);
        }
    }
    hd_unlock();
}

static void set_overlay(const char *title, const char *sub, const char *mark, bool has_until, double until)
{
    overlay_t *o = &D.overlay;
    o->active = true;
    snprintf(o->title, sizeof(o->title), "%s", title);
    snprintf(o->sub, sizeof(o->sub), "%s", sub);
    snprintf(o->mark, sizeof(o->mark), "%s", mark);
    o->has_until = has_until;
    o->until = until;
}

void display_on_event(status_t status, action_t action, const char *detail, double now)
{
    (void)detail;
    char title[40] = "";
    if (action >= 0 && action < ACT_COUNT) {
        snprintf(title, sizeof(title), "%s", EVENT_LABELS[action]);
        upper(title);
    }
    hd_lock();
    switch (status) {
    case ST_SENDING:
        D.last_input = now;
        set_overlay(title, "logging...", "", false, 0);
        break;
    case ST_SUCCESS: {
        char clock[16];
        clock_string(now, clock, sizeof(clock));
        set_overlay(title, clock, "check", true, now + SUCCESS_SECONDS);
        break;
    }
    case ST_RETRYING:
        set_overlay(title, "no connection - retrying", "", false, 0);
        break;
    case ST_FAILED:
        set_overlay(title, "NOT LOGGED", "cross", true, now + FAILED_SECONDS);
        break;
    case ST_REMOTE:
        set_overlay(title, "from the app", "", true, now + REMOTE_SECONDS);
        break;
    case ST_IGNORED:
        break;
    }
    hd_unlock();
}

void display_on_sleep_doc(const sleep_doc_t *doc)
{
    hd_lock();
    bool running = doc->timer_active && doc->has_timer_start && doc->timer_start_ms != 0;
    D.state.has_sleep_start = running;
    D.state.sleep_start = running ? doc->timer_start_ms / 1000 : 0; /* sleep timer is in ms */
    hd_unlock();
}

void display_on_diaper_doc(const diaper_doc_t *doc)
{
    hd_lock();
    if (doc->has_last) {
        D.state.has_last_diaper = true;
        D.state.last_diaper_start = doc->last_start;
        snprintf(D.state.last_diaper_mode, sizeof(D.state.last_diaper_mode), "%s", doc->last_mode);
    }
    hd_unlock();
}

static void set_last_feed(const feed_doc_t *doc)
{
    deck_state_t *s = &D.state;
    if (!doc->has_last_nursing && !doc->has_last_bottle)
        return;
    double nursing_start = doc->has_last_nursing ? epoch_seconds(doc->last_nursing_start) : 0;
    double bottle_start = doc->has_last_bottle ? epoch_seconds(doc->last_bottle_start) : 0;
    if (!doc->has_last_bottle || (doc->has_last_nursing && nursing_start >= bottle_start)) {
        char detail[24] = "";
        if (doc->last_nursing_duration != 0)
            format_duration(doc->last_nursing_duration, detail, sizeof(detail));
        const char *side = doc->prefs_last_side[0] ? doc->prefs_last_side : "none";
        if (strcmp(side, "none")) {
            size_t len = strlen(detail);
            snprintf(detail + len, sizeof(detail) - len, "%s%c", len ? " " : "", side[0] - 32);
        }
        s->has_last_feed = true;
        s->last_feed_start = nursing_start;
        snprintf(s->last_feed_kind, sizeof(s->last_feed_kind), "nursing");
        snprintf(s->last_feed_detail, sizeof(s->last_feed_detail), "%s", detail);
    } else {
        char detail[24] = "";
        if (doc->last_bottle_amount != 0)
            snprintf(detail, sizeof(detail), "%g%s", doc->last_bottle_amount, doc->last_bottle_units);
        s->has_last_feed = true;
        s->last_feed_start = bottle_start;
        snprintf(s->last_feed_kind, sizeof(s->last_feed_kind), "bottle");
        snprintf(s->last_feed_detail, sizeof(s->last_feed_detail), "%s", detail);
    }
}

void display_on_feed_doc(const feed_doc_t *doc)
{
    hd_lock();
    deck_state_t *s = &D.state;
    s->nursing_active = doc->timer_active;
    if (s->nursing_active) {
        s->nursing_paused = doc->timer_paused;
        const char *side = doc->active_side[0] ? doc->active_side : doc->last_side;
        snprintf(s->nursing_side, sizeof(s->nursing_side), "%s", !strcmp(side, "none") ? "" : side);
        s->nursing_banked = doc->left_duration + doc->right_duration;
        s->has_nursing_segment = doc->has_timer_start && doc->timer_start != 0;
        /* the iOS app stores this in ms (py-huckleberry-api #51); seconds either way */
        s->nursing_segment_start = s->has_nursing_segment ? epoch_seconds(doc->timer_start) : 0;
    }
    set_last_feed(doc);
    hd_unlock();
}

void display_set_totals(int diapers, int nursing_count, double nursing_seconds)
{
    hd_lock();
    D.state.has_totals = true;
    D.state.diapers_today = diapers;
    D.state.nursing_count_today = nursing_count;
    D.state.nursing_seconds_today = nursing_seconds;
    hd_unlock();
}
