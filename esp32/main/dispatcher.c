#include "dispatcher.h"

#include <stdio.h>
#include <string.h>
#include <time.h>

#include "huck_api.h"
#include "platform.h"

static const char *TAG = "dispatcher";

#define QUEUE_SIZE 16

typedef struct {
    action_t action;
    double pressed_at;     /* epoch seconds (valid when time_valid) */
    double pressed_uptime; /* to re-derive pressed_at if the clock wasn't set yet */
    bool time_valid;
    session_kind_t kind;   /* KIND_SLEEP/KIND_NURSING for toggle presses, else KIND_NONE */
    bool resolved;         /* toggle action confirmed against the server */
} event_t;

static struct {
    double debounce;
    status_cb_t on_event;
    event_t queue[QUEUE_SIZE];
    int head, count;
    double last_press[BTN_COUNT];
    bool has_last_press[BTN_COUNT];
    bool active[KIND_COUNT];  /* local state, persisted */
    bool remote[KIND_COUNT];  /* last state known to be on the server */
    int pending[KIND_COUNT];  /* toggle presses still in flight */
} S;

static session_kind_t toggle_kind(button_event_t event)
{
    switch (event) {
    case BTN_SLEEP_TOGGLE: return KIND_SLEEP;
    case BTN_NURSING_TOGGLE: return KIND_NURSING;
    default: return KIND_NONE;
    }
}

static action_t start_action(session_kind_t kind)
{
    return kind == KIND_SLEEP ? ACT_SLEEP_START : ACT_NURSING_START;
}

static action_t stop_action(session_kind_t kind)
{
    return kind == KIND_SLEEP ? ACT_SLEEP_STOP : ACT_NURSING_STOP;
}

static action_t plain_action(button_event_t event)
{
    switch (event) {
    case BTN_PEE: return ACT_PEE;
    case BTN_POO: return ACT_POO;
    case BTN_BOTH: return ACT_BOTH;
    case BTN_BOTTLE: return ACT_BOTTLE;
    default: return ACT_NONE;
    }
}

static void set_active(session_kind_t kind, bool active)
{
    if (S.active[kind] != active) {
        S.active[kind] = active;
        hd_state_save(S.active[KIND_SLEEP], S.active[KIND_NURSING]);
    }
}

void dispatcher_init(double debounce_seconds, status_cb_t on_event)
{
    memset(&S, 0, sizeof(S));
    S.debounce = debounce_seconds;
    S.on_event = on_event;
    hd_state_load(&S.active[KIND_SLEEP], &S.active[KIND_NURSING]);
    for (int k = 0; k < KIND_COUNT; k++)
        S.remote[k] = S.active[k];
}

bool dispatcher_sleep_active(void)
{
    hd_lock();
    bool v = S.active[KIND_SLEEP];
    hd_unlock();
    return v;
}

bool dispatcher_nursing_active(void)
{
    hd_lock();
    bool v = S.active[KIND_NURSING];
    hd_unlock();
    return v;
}

bool dispatcher_idle(void)
{
    hd_lock();
    bool idle = S.count == 0;
    hd_unlock();
    return idle;
}

void dispatcher_sync_remote(session_kind_t kind, bool active)
{
    hd_lock();
    S.remote[kind] = active;
    if (S.pending[kind] || S.active[kind] == active) {
        hd_unlock();
        return; /* our own press is in flight / echo of what we already know */
    }
    set_active(kind, active);
    hd_unlock();
    action_t action = active ? start_action(kind) : stop_action(kind);
    char detail[64];
    snprintf(detail, sizeof(detail), "%s (synced from Huckleberry)", EVENT_LABELS[action]);
    S.on_event(ST_REMOTE, action, detail);
}

static void settle(session_kind_t kind)
{
    S.pending[kind]--;
    if (!S.pending[kind])
        set_active(kind, S.remote[kind]);
}

void dispatcher_press(button_event_t event)
{
    if (event < 0 || event >= BTN_COUNT)
        return;
    double uptime = hd_uptime();
    hd_lock();
    if (S.has_last_press[event] && uptime - S.last_press[event] < S.debounce) {
        hd_unlock();
        char detail[48];
        snprintf(detail, sizeof(detail), "double-press: %s", BUTTON_EVENT_NAMES[event]);
        S.on_event(ST_IGNORED, ACT_NONE, detail);
        return;
    }
    S.last_press[event] = uptime;
    S.has_last_press[event] = true;

    event_t ev = {0};
    ev.kind = toggle_kind(event);
    ev.pressed_at = hd_now();
    ev.pressed_uptime = uptime;
    ev.time_valid = hd_time_valid();
    if (ev.kind != KIND_NONE) {
        bool active = S.active[ev.kind];
        ev.action = active ? stop_action(ev.kind) : start_action(ev.kind);
        S.pending[ev.kind]++;
        set_active(ev.kind, !active);
    } else {
        ev.action = plain_action(event);
    }
    if (S.count >= QUEUE_SIZE) {
        hd_unlock();
        hd_log(TAG, "Queue full; dropping %s", EVENT_LABELS[ev.action]);
        return;
    }
    S.queue[(S.head + S.count) % QUEUE_SIZE] = ev;
    S.count++;
    hd_unlock();
    S.on_event(ST_SENDING, ev.action, EVENT_LABELS[ev.action]);
    hd_event_signal();
}

/* Let the server's timer state, not our guess, pick start vs. stop.
 * Only until the first successful read: if a write then times out after
 * actually landing, re-resolving on the retry would undo it. */
static int resolve_toggle(event_t *ev)
{
    bool active;
    if (huck_session_active(ev->kind, &active))
        return -1;
    ev->resolved = true;
    hd_lock();
    S.remote[ev->kind] = active;
    action_t action = active ? stop_action(ev->kind) : start_action(ev->kind);
    bool changed = action != ev->action;
    if (changed) {
        ev->action = action;
        set_active(ev->kind, !active);
    }
    hd_unlock();
    if (changed) {
        char detail[96];
        snprintf(detail, sizeof(detail), "%s (session was %s in Huckleberry)", EVENT_LABELS[action],
                 active ? "already running" : "not running");
        S.on_event(ST_SENDING, action, detail);
    }
    return 0;
}

static double pressed_at(const event_t *ev)
{
    if (ev->time_valid)
        return ev->pressed_at;
    /* the clock was still unset at press time: back-date from uptime */
    return hd_now() - (hd_uptime() - ev->pressed_uptime);
}

static int send(const event_t *ev)
{
    switch (ev->action) {
    case ACT_PEE: return huck_log_diaper("pee", pressed_at(ev));
    case ACT_POO: return huck_log_diaper("poo", pressed_at(ev));
    case ACT_BOTH: return huck_log_diaper("both", pressed_at(ev));
    case ACT_BOTTLE: return huck_log_bottle(pressed_at(ev));
    case ACT_SLEEP_START: return huck_start_sleep();
    case ACT_SLEEP_STOP: return huck_complete_sleep();
    case ACT_NURSING_START: return huck_start_nursing();
    case ACT_NURSING_STOP: return huck_complete_nursing();
    default: return -1;
    }
}

static void stamp(const event_t *ev, char *out, int n)
{
    time_t t = (time_t)pressed_at(ev);
    struct tm tm;
    localtime_r(&t, &tm);
    strftime(out, n, "%H:%M:%S", &tm);
}

static void send_with_retry(event_t *ev)
{
    char when[16], detail[128];
    stamp(ev, when, sizeof(when));
    for (int attempt = 1; attempt <= DISPATCHER_RETRIES; attempt++) {
        int rc = 0;
        if (ev->kind != KIND_NONE && !ev->resolved)
            rc = resolve_toggle(ev);
        if (!rc)
            rc = send(ev);
        if (!rc) {
            if (ev->kind != KIND_NONE) {
                hd_lock();
                S.remote[ev->kind] = ev->action == ACT_SLEEP_START || ev->action == ACT_NURSING_START;
                settle(ev->kind);
                hd_unlock();
            }
            snprintf(detail, sizeof(detail), "%s @ %s", EVENT_LABELS[ev->action], when);
            S.on_event(ST_SUCCESS, ev->action, detail);
            return;
        }
        if (attempt < DISPATCHER_RETRIES) {
            int delay = 1;
            for (int i = 0; i < attempt; i++)
                delay *= DISPATCHER_BACKOFF_BASE_SECONDS;
            snprintf(detail, sizeof(detail), "%s failed; retry %d/%d in %ds", EVENT_LABELS[ev->action], attempt,
                     DISPATCHER_RETRIES - 1, delay);
            S.on_event(ST_RETRYING, ev->action, detail);
            hd_sleep_ms(delay * 1000);
        } else {
            hd_log(TAG, "Giving up on %s pressed at %s", EVENT_LABELS[ev->action], when);
            if (ev->kind != KIND_NONE) {
                hd_lock();
                settle(ev->kind); /* drop the optimistic guess */
                hd_unlock();
            }
            snprintf(detail, sizeof(detail), "%s @ %s LOST after %d attempts", EVENT_LABELS[ev->action], when,
                     DISPATCHER_RETRIES);
            S.on_event(ST_FAILED, ev->action, detail);
        }
    }
}

void dispatcher_process(void)
{
    for (;;) {
        hd_lock();
        if (!S.count) {
            hd_unlock();
            return;
        }
        event_t ev = S.queue[S.head];
        hd_unlock();
        send_with_retry(&ev);
        hd_lock();
        S.head = (S.head + 1) % QUEUE_SIZE;
        S.count--;
        hd_unlock();
    }
}
