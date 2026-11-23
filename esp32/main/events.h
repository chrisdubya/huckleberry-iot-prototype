/* Deck events and the status callback contract shared by dispatcher, LED and display.
 * Mirrors EVENT_LABELS / StatusCallback in src/huckdeck/dispatcher.py. */
#pragma once

typedef enum {
    ACT_PEE,
    ACT_POO,
    ACT_BOTH,
    ACT_BOTTLE,
    ACT_SLEEP_START,
    ACT_SLEEP_STOP,
    ACT_NURSING_START,
    ACT_NURSING_STOP,
    ACT_COUNT,
    ACT_NONE = -1, /* "ignored" status carries no action */
} action_t;

/* What a button is bound to (config.yaml `buttons:` values). */
typedef enum {
    BTN_PEE,
    BTN_POO,
    BTN_BOTH,
    BTN_BOTTLE,
    BTN_SLEEP_TOGGLE,
    BTN_NURSING_TOGGLE,
    BTN_COUNT,
} button_event_t;

typedef enum {
    KIND_SLEEP,
    KIND_NURSING,
    KIND_COUNT,
    KIND_NONE = -1,
} session_kind_t;

typedef enum {
    ST_SENDING,
    ST_SUCCESS,
    ST_RETRYING,
    ST_FAILED,
    ST_IGNORED,
    ST_REMOTE,
} status_t;

static const char *const EVENT_LABELS[ACT_COUNT] = {
    [ACT_PEE] = "Pee diaper",
    [ACT_POO] = "Poop diaper",
    [ACT_BOTH] = "Pee+poop diaper",
    [ACT_BOTTLE] = "Bottle",
    [ACT_SLEEP_START] = "Sleep started",
    [ACT_SLEEP_STOP] = "Sleep completed",
    [ACT_NURSING_START] = "Nursing started",
    [ACT_NURSING_STOP] = "Nursing completed",
};

static const char *const BUTTON_EVENT_NAMES[BTN_COUNT] = {
    "pee", "poo", "both", "bottle", "sleep_toggle", "nursing_toggle",
};

/* on_event(status, action, detail) — detail is free display text. */
typedef void (*status_cb_t)(status_t status, action_t action, const char *detail);
