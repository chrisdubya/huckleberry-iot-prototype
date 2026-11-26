/* Huckleberry over Firestore: the deck's share of py-huckleberry-api
 * (authenticate, pick a child, log diapers/bottles, start/complete sleep
 * and nursing, read timers, daily totals), writing the same documents.
 */
#pragma once

#include <stdbool.h>

#include "cJSON.h"
#include "display.h"
#include "events.h"

typedef struct {
    double amount;          /* bottle: amount */
    const char *units;      /* "ml" / "oz" */
    const char *type;       /* "Formula", "Breast Milk", ... */
    const char *nursing_side; /* "left" / "right" */
    const char *child_uid;  /* "" = first child on the account */
} huck_config_t;

void huck_init(const huck_config_t *config);
/* Authenticate and resolve which child to log against. 0 on success. */
int huck_connect(void);
const char *huck_child_uid(void);
const char *huck_child_name(void);

/* -- deck events (dispatcher) ------------------------------------------- */
int huck_session_active(session_kind_t kind, bool *active);
int huck_log_diaper(const char *mode, double pressed_at);
int huck_log_bottle(double pressed_at);
int huck_start_sleep(void);
int huck_complete_sleep(void);
int huck_start_nursing(void);
int huck_complete_nursing(void);

/* -- live documents (poll task) ----------------------------------------- */
/* One batchGet of sleep/feed/diaper; cb for each document whose updateTime
 * changed since the last poll (and for all of them on the first). */
typedef void (*huck_doc_cb_t)(const char *collection, const cJSON *fields);
int huck_poll(huck_doc_cb_t cb);
void huck_parse_sleep(const cJSON *fields, sleep_doc_t *out);
void huck_parse_feed(const cJSON *fields, feed_doc_t *out);
void huck_parse_diaper(const cJSON *fields, diaper_doc_t *out);

/* -- daily totals (display) --------------------------------------------- */
typedef struct {
    double day_start; /* epoch seconds */
    int diapers;
    int nursing_count;
    double nursing_seconds;
} day_totals_t;
double huck_day_start(double now, int day_start_hour);
int huck_day_totals(int day_start_hour, day_totals_t *out);
/* Timezone offset in minutes, Huckleberry's sign (negative for UTC+). */
double huck_tz_offset_minutes(double now);
