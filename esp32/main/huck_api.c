#include "huck_api.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "firebase_auth.h"
#include "firestore.h"
#include "platform.h"

static const char *TAG = "huck";

static struct {
    huck_config_t config;
    char child_uid[64];
    char child_name[64];
    char last_update[3][40]; /* sleep, feed, diaper updateTime from the last poll */
    bool polled_once;
} H;

static const char *COLLECTIONS[3] = {"sleep", "feed", "diaper"};

void huck_init(const huck_config_t *config)
{
    memset(&H, 0, sizeof(H));
    H.config = *config;
}

const char *huck_child_uid(void)
{
    return H.child_uid;
}

const char *huck_child_name(void)
{
    return H.child_name;
}

static void doc_path(char *out, int n, const char *collection, const char *suffix)
{
    snprintf(out, n, "%s/%s%s", collection, H.child_uid, suffix ? suffix : "");
}

double huck_tz_offset_minutes(double now)
{
    time_t t = (time_t)now;
    struct tm lt, gt;
    localtime_r(&t, &lt);
    gmtime_r(&t, &gt);
    long local = lt.tm_hour * 3600L + lt.tm_min * 60L + lt.tm_sec;
    long utc = gt.tm_hour * 3600L + gt.tm_min * 60L + gt.tm_sec;
    long diff = local - utc;
    int day_delta = lt.tm_yday - gt.tm_yday;
    if (lt.tm_year != gt.tm_year)
        day_delta = lt.tm_year > gt.tm_year ? 1 : -1;
    diff += day_delta * 86400L;
    return -(double)diff / 60.0; /* negative for UTC+, like the library */
}

/* -- connect ------------------------------------------------------------ */

int huck_connect(void)
{
    if (firebase_auth_ensure())
        return -1;
    char path[96];
    snprintf(path, sizeof(path), "users/%s", firebase_auth_user_uid());
    fs_doc_t user;
    if (fs_get_document(path, &user) || !user.exists) {
        hd_log(TAG, "User document not found");
        fs_doc_free(&user);
        return -1;
    }
    const cJSON *list = fs_get(user.fields, "childList");
    const cJSON *av = list ? cJSON_GetObjectItemCaseSensitive(list, "arrayValue") : NULL;
    const cJSON *values = av ? cJSON_GetObjectItemCaseSensitive(av, "values") : NULL;
    int count = values ? cJSON_GetArraySize(values) : 0;
    if (!count) {
        hd_log(TAG, "No children found on this Huckleberry account");
        fs_doc_free(&user);
        return -1;
    }
    const cJSON *match = NULL;
    const char *pinned = H.config.child_uid ? H.config.child_uid : "";
    const cJSON *item;
    cJSON_ArrayForEach(item, values) {
        const cJSON *mv = cJSON_GetObjectItemCaseSensitive(item, "mapValue");
        const cJSON *fields = mv ? cJSON_GetObjectItemCaseSensitive(mv, "fields") : NULL;
        const char *cid = fs_get_string(fields, "cid");
        if (!cid)
            continue;
        if (*pinned ? !strcmp(cid, pinned) : match == NULL) {
            match = fields;
            if (*pinned)
                break;
        }
    }
    if (!match) {
        hd_log(TAG, "child_uid %s not found on account", pinned);
        fs_doc_free(&user);
        return -1;
    }
    if (!*pinned && count > 1)
        hd_log(TAG, "Account has %d children; using the first. Pin one with HUCKDECK_CHILD_UID", count);
    snprintf(H.child_uid, sizeof(H.child_uid), "%s", fs_get_string(match, "cid"));
    const char *nick = fs_get_string(match, "nickname");
    snprintf(H.child_name, sizeof(H.child_name), "%s", nick && *nick ? nick : H.child_uid);
    fs_doc_free(&user);
    return 0;
}

/* -- document parsing --------------------------------------------------- */

static void copy_string(char *out, int n, const cJSON *fields, const char *path)
{
    const char *s = fs_get_string(fields, path);
    snprintf(out, n, "%s", s ? s : "");
}

void huck_parse_sleep(const cJSON *fields, sleep_doc_t *out)
{
    memset(out, 0, sizeof(*out));
    fs_get_bool(fields, "timer.active", &out->timer_active);
    out->has_timer_start = fs_get_number(fields, "timer.timerStartTime", &out->timer_start_ms);
}

void huck_parse_feed(const cJSON *fields, feed_doc_t *out)
{
    memset(out, 0, sizeof(*out));
    fs_get_bool(fields, "timer.active", &out->timer_active);
    fs_get_bool(fields, "timer.paused", &out->timer_paused);
    out->has_feed_start = fs_get_number(fields, "timer.feedStartTime", &out->feed_start);
    out->has_timer_start = fs_get_number(fields, "timer.timerStartTime", &out->timer_start);
    fs_get_number(fields, "timer.leftDuration", &out->left_duration);
    fs_get_number(fields, "timer.rightDuration", &out->right_duration);
    copy_string(out->active_side, sizeof(out->active_side), fields, "timer.activeSide");
    copy_string(out->last_side, sizeof(out->last_side), fields, "timer.lastSide");
    out->has_last_nursing = fs_get_number(fields, "prefs.lastNursing.start", &out->last_nursing_start);
    fs_get_number(fields, "prefs.lastNursing.duration", &out->last_nursing_duration);
    out->has_last_bottle = fs_get_number(fields, "prefs.lastBottle.start", &out->last_bottle_start);
    fs_get_number(fields, "prefs.lastBottle.bottleAmount", &out->last_bottle_amount);
    copy_string(out->last_bottle_units, sizeof(out->last_bottle_units), fields, "prefs.lastBottle.bottleUnits");
    copy_string(out->prefs_last_side, sizeof(out->prefs_last_side), fields, "prefs.lastSide.lastSide");
}

void huck_parse_diaper(const cJSON *fields, diaper_doc_t *out)
{
    memset(out, 0, sizeof(*out));
    out->has_last = fs_get_number(fields, "prefs.lastDiaper.start", &out->last_start);
    copy_string(out->last_mode, sizeof(out->last_mode), fields, "prefs.lastDiaper.mode");
}

/* -- live session state ------------------------------------------------- */

int huck_session_active(session_kind_t kind, bool *active)
{
    char path[96];
    doc_path(path, sizeof(path), kind == KIND_SLEEP ? "sleep" : "feed", NULL);
    fs_doc_t doc;
    if (fs_get_document(path, &doc))
        return -1;
    bool a = false;
    if (doc.exists)
        fs_get_bool(doc.fields, "timer.active", &a);
    *active = a; /* paused counts as active, like the library */
    fs_doc_free(&doc);
    return 0;
}

/* -- shared payload bits ------------------------------------------------ */

static cJSON *timestamp_map(double seconds)
{
    cJSON *m = fs_map();
    fs_map_add(m, "seconds", fs_double(seconds));
    return m;
}

static void interval_id(char *out, int n, double now)
{
    char rnd[24];
    hd_random_hex(rnd, 20);
    snprintf(out, n, "%lld-%s", (long long)(now * 1000), rnd);
}

/* -- diaper ------------------------------------------------------------- */

int huck_log_diaper(const char *mode, double pressed_at)
{
    double now = hd_now();
    double offset = huck_tz_offset_minutes(now);
    char path[96];
    doc_path(path, sizeof(path), "diaper", NULL);
    fs_doc_t doc;
    if (fs_get_document(path, &doc))
        return -1;
    double existing_start;
    bool should_update_last = !doc.exists || !fs_get_number(doc.fields, "prefs.lastDiaper.start", &existing_start) ||
                              pressed_at >= existing_start;
    fs_doc_free(&doc);

    char id[48], ipath[160];
    interval_id(id, sizeof(id), now);
    snprintf(ipath, sizeof(ipath), "%s/intervals/%s", path, id);
    cJSON *interval = fs_map();
    fs_map_add(interval, "start", fs_double(pressed_at));
    fs_map_add(interval, "lastUpdated", fs_double(now));
    fs_map_add(interval, "mode", fs_str(mode));
    fs_map_add(interval, "offset", fs_double(offset));
    if (fs_set(ipath, interval))
        return -1;
    hd_log(TAG, "Created diaper interval %s", id);

    if (should_update_last) {
        cJSON *last = fs_map();
        fs_map_add(last, "start", fs_double(pressed_at));
        fs_map_add(last, "mode", fs_str(mode));
        fs_map_add(last, "offset", fs_double(offset));
        cJSON *prefs = fs_map();
        fs_map_add(prefs, "lastDiaper", last);
        fs_map_add(prefs, "timestamp", timestamp_map(now));
        fs_map_add(prefs, "local_timestamp", fs_double(now));
        cJSON *root = fs_map();
        fs_map_add(root, "prefs", prefs);
        if (fs_set_merge(path, root))
            return -1;
    }
    return 0;
}

/* -- bottle ------------------------------------------------------------- */

int huck_log_bottle(double pressed_at)
{
    double now = hd_now();
    double offset = huck_tz_offset_minutes(now);
    double amount = H.config.amount;
    const char *type = H.config.type, *units = H.config.units;
    char path[96];
    doc_path(path, sizeof(path), "feed", NULL);
    fs_doc_t doc;
    if (fs_get_document(path, &doc))
        return -1;
    double existing_start;
    bool should_update_last = !doc.exists || !fs_get_number(doc.fields, "prefs.lastBottle.start", &existing_start) ||
                              pressed_at >= existing_start;
    fs_doc_free(&doc);

    char id[48], ipath[160];
    interval_id(id, sizeof(id), now);
    snprintf(ipath, sizeof(ipath), "%s/intervals/%s", path, id);
    cJSON *entry = fs_map();
    fs_map_add(entry, "mode", fs_str("bottle"));
    fs_map_add(entry, "start", fs_double(pressed_at));
    fs_map_add(entry, "lastUpdated", fs_double(now));
    fs_map_add(entry, "bottleType", fs_str(type));
    fs_map_add(entry, "amount", fs_double(amount));
    fs_map_add(entry, "units", fs_str(units));
    fs_map_add(entry, "offset", fs_double(offset));
    fs_map_add(entry, "end_offset", fs_double(offset));
    if (fs_set(ipath, entry))
        return -1;
    hd_log(TAG, "Created bottle interval %s", id);

    if (should_update_last) {
        cJSON *last = fs_map();
        fs_map_add(last, "mode", fs_str("bottle"));
        fs_map_add(last, "start", fs_double(pressed_at));
        fs_map_add(last, "bottleType", fs_str(type));
        fs_map_add(last, "bottleAmount", fs_double(amount));
        fs_map_add(last, "bottleUnits", fs_str(units));
        fs_map_add(last, "offset", fs_double(offset));
        cJSON *prefs = fs_map();
        fs_map_add(prefs, "lastBottle", last);
        fs_map_add(prefs, "bottleType", fs_str(type));
        fs_map_add(prefs, "bottleAmount", fs_double(amount));
        fs_map_add(prefs, "bottleUnits", fs_str(units));
        fs_map_add(prefs, "timestamp", timestamp_map(now));
        fs_map_add(prefs, "local_timestamp", fs_double(now));
        cJSON *root = fs_map();
        fs_map_add(root, "prefs", prefs);
        if (fs_set_merge(path, root))
            return -1;
    }
    return 0;
}

/* -- sleep -------------------------------------------------------------- */

static cJSON *bool_map(const char *const *keys, int n)
{
    cJSON *m = fs_map();
    for (int i = 0; i < n; i++)
        fs_map_add(m, keys[i], fs_bool(false));
    return m;
}

int huck_start_sleep(void)
{
    static const char *const START[] = {"happy", "longTimeToFallAsleep", "10-20_minutes", "upset", "under_10_minutes"};
    static const char *const LOCATIONS[] = {"car", "nursing", "wornOrHeld", "stroller", "coSleep", "nextToCarer", "onOwnInBed", "bottle", "swing"};
    static const char *const END[] = {"happy", "wokeUpChild", "upset"};
    double now = hd_now();
    char uuid[20];
    hd_random_hex(uuid, 16);
    cJSON *details = fs_map();
    fs_map_add(details, "startSleepCondition", bool_map(START, 5));
    fs_map_add(details, "sleepLocations", bool_map(LOCATIONS, 9));
    fs_map_add(details, "endSleepCondition", bool_map(END, 3));
    cJSON *timer = fs_map();
    fs_map_add(timer, "active", fs_bool(true));
    fs_map_add(timer, "paused", fs_bool(false));
    fs_map_add(timer, "timestamp", timestamp_map(now));
    fs_map_add(timer, "local_timestamp", fs_double(now));
    fs_map_add(timer, "timerStartTime", fs_double(now * 1000)); /* milliseconds for sleep */
    fs_map_add(timer, "uuid", fs_str(uuid));
    fs_map_add(timer, "details", details);
    cJSON *root = fs_map();
    fs_map_add(root, "timer", timer);
    char path[96];
    doc_path(path, sizeof(path), "sleep", NULL);
    return fs_set_merge(path, root);
}

int huck_complete_sleep(void)
{
    char path[96];
    doc_path(path, sizeof(path), "sleep", NULL);
    fs_doc_t doc;
    if (fs_get_document(path, &doc))
        return -1;
    if (!doc.exists) {
        hd_log(TAG, "No active sleep document to complete");
        fs_doc_free(&doc);
        return 0;
    }
    bool active = false;
    fs_get_bool(doc.fields, "timer.active", &active);
    if (!active) {
        hd_log(TAG, "Sleep already completed, ignoring duplicate request");
        fs_doc_free(&doc);
        return 0;
    }
    double timer_start_ms = 0, ts_seconds = 0;
    if (!fs_get_number(doc.fields, "timer.timerStartTime", &timer_start_ms) || timer_start_ms == 0) {
        if (fs_get_number(doc.fields, "timer.timestamp.seconds", &ts_seconds) && ts_seconds != 0) {
            timer_start_ms = (double)(long long)(ts_seconds * 1000);
            hd_log(TAG, "timerStartTime missing; falling back to timestamp.seconds");
        } else {
            hd_log(TAG, "Missing timerStartTime; cannot compute duration");
            fs_doc_free(&doc);
            fs_update_op_t del = {"timer", NULL};
            return fs_update(path, &del, 1);
        }
    }
    double now = hd_now();
    double now_ms = now * 1000;
    bool paused = false;
    double end_ms = now_ms, timer_end_ms;
    fs_get_bool(doc.fields, "timer.paused", &paused);
    if (paused && fs_get_number(doc.fields, "timer.timerEndTime", &timer_end_ms))
        end_ms = timer_end_ms;
    long long duration_sec = (long long)((end_ms - timer_start_ms) / 1000);
    long long start_sec = (long long)(timer_start_ms / 1000);
    double offset = huck_tz_offset_minutes(now);
    char uuid[20];
    const char *existing_uuid = fs_get_string(doc.fields, "timer.uuid");
    if (existing_uuid)
        snprintf(uuid, sizeof(uuid), "%s", existing_uuid);
    else
        hd_random_hex(uuid, 16);
    const cJSON *details = fs_get(doc.fields, "timer.details");

    char id[20], ipath[160];
    hd_random_hex(id, 16);
    snprintf(ipath, sizeof(ipath), "%s/intervals/%s", path, id);
    cJSON *interval = fs_map();
    fs_map_add(interval, "start", fs_int(start_sec));
    fs_map_add(interval, "duration", fs_int(duration_sec));
    fs_map_add(interval, "offset", fs_double(offset));
    fs_map_add(interval, "end_offset", fs_double(offset));
    if (details)
        fs_map_add(interval, "details", cJSON_Duplicate(details, true));
    fs_map_add(interval, "lastUpdated", fs_double(now));
    fs_doc_free(&doc);
    if (fs_set(ipath, interval))
        return -1;

    cJSON *timer = fs_map();
    fs_map_add(timer, "active", fs_bool(false));
    fs_map_add(timer, "paused", fs_bool(false));
    fs_map_add(timer, "timestamp", timestamp_map(now));
    fs_map_add(timer, "timerStartTime", fs_null());
    fs_map_add(timer, "uuid", fs_str(uuid));
    fs_map_add(timer, "local_timestamp", fs_double(now));
    cJSON *last = fs_map();
    fs_map_add(last, "start", fs_int(start_sec));
    fs_map_add(last, "duration", fs_int(duration_sec));
    fs_map_add(last, "offset", fs_double(offset));
    fs_update_op_t ops[] = {
        {"timer", timer},
        {"prefs.lastSleep", last},
        {"prefs.timestamp", timestamp_map(now)},
        {"prefs.local_timestamp", fs_double(now)},
    };
    if (fs_update(path, ops, 4))
        return -1;
    hd_log(TAG, "Sleep completed (duration %llds)", duration_sec);
    return 0;
}

/* -- nursing ------------------------------------------------------------ */

int huck_start_nursing(void)
{
    double now = hd_now();
    char uuid[20];
    hd_random_hex(uuid, 16);
    cJSON *timer = fs_map();
    fs_map_add(timer, "active", fs_bool(true));
    fs_map_add(timer, "paused", fs_bool(false));
    fs_map_add(timer, "timestamp", timestamp_map(now));
    fs_map_add(timer, "local_timestamp", fs_double(now));
    fs_map_add(timer, "feedStartTime", fs_double(now));
    fs_map_add(timer, "timerStartTime", fs_double(now));
    fs_map_add(timer, "uuid", fs_str(uuid));
    fs_map_add(timer, "leftDuration", fs_double(0));
    fs_map_add(timer, "rightDuration", fs_double(0));
    fs_map_add(timer, "lastSide", fs_str("left"));
    fs_map_add(timer, "activeSide", fs_str(H.config.nursing_side));
    cJSON *root = fs_map();
    fs_map_add(root, "timer", timer);
    char path[96];
    doc_path(path, sizeof(path), "feed", NULL);
    return fs_set_merge(path, root);
}

/* Rewrite millisecond nursing timer fields as seconds before completing: a
 * session started in the iOS app has feedStartTime in milliseconds, which
 * would otherwise land in history thousands of years off (py-huckleberry-api #51). */
static int fix_timer_units(const char *path, const cJSON *fields)
{
    static const char *const FIELDS[] = {"timer.feedStartTime", "timer.timerStartTime"};
    fs_update_op_t ops[2];
    int n = 0;
    for (int i = 0; i < 2; i++) {
        double v;
        if (fs_get_number(fields, FIELDS[i], &v) && is_millis(v)) {
            ops[n].path = FIELDS[i];
            ops[n].value = fs_double(v / 1000);
            n++;
        }
    }
    if (!n)
        return 0;
    hd_log(TAG, "Nursing timer has millisecond fields (started from the app?); rewriting as seconds");
    return fs_update(path, ops, n);
}

int huck_complete_nursing(void)
{
    char path[96];
    doc_path(path, sizeof(path), "feed", NULL);
    fs_doc_t doc;
    if (fs_get_document(path, &doc))
        return -1;
    if (!doc.exists) {
        hd_log(TAG, "No active feed document to complete");
        fs_doc_free(&doc);
        return 0;
    }
    if (fix_timer_units(path, doc.fields)) {
        fs_doc_free(&doc);
        return -1;
    }
    bool active = false;
    fs_get_bool(doc.fields, "timer.active", &active);
    if (!active) {
        hd_log(TAG, "Nursing already completed, ignoring duplicate request");
        fs_doc_free(&doc);
        return 0;
    }
    double timer_start;
    if (!fs_get_number(doc.fields, "timer.timerStartTime", &timer_start) || timer_start == 0) {
        hd_log(TAG, "Missing timerStartTime for nursing");
        fs_doc_free(&doc);
        return 0;
    }
    timer_start = epoch_seconds(timer_start); /* just rewritten if it was in ms */
    double now = hd_now();
    double left = 0, right = 0;
    fs_get_number(doc.fields, "timer.leftDuration", &left);
    fs_get_number(doc.fields, "timer.rightDuration", &right);
    bool paused = false;
    fs_get_bool(doc.fields, "timer.paused", &paused);
    const char *active_side = fs_get_string(doc.fields, "timer.activeSide");
    const char *last_side = fs_get_string(doc.fields, "timer.lastSide");
    if (!paused) {
        double elapsed = now - timer_start;
        const char *side = active_side && *active_side ? active_side : last_side && *last_side ? last_side : "left";
        if (!strcmp(side, "left"))
            left += elapsed;
        else
            right += elapsed;
    }
    double total = left + right;
    /* feedStartTime as stored (int or double) so the history row keeps its type */
    const cJSON *feed_start_value = fs_get(doc.fields, "timer.feedStartTime");
    double feed_start;
    bool has_feed_start = fs_get_number(doc.fields, "timer.feedStartTime", &feed_start) && feed_start != 0;
    if (has_feed_start)
        feed_start = epoch_seconds(feed_start);
    else
        feed_start = timer_start;
    const char *last_side_value = active_side && *active_side ? active_side : last_side && *last_side ? last_side : "right";
    if (!strcmp(last_side_value, "none"))
        last_side_value = right >= left ? "right" : "left";
    double offset = huck_tz_offset_minutes(now);

    char id[48], ipath[160];
    interval_id(id, sizeof(id), now);
    snprintf(ipath, sizeof(ipath), "%s/intervals/%s", path, id);
    cJSON *interval = fs_map();
    fs_map_add(interval, "mode", fs_str("breast"));
    fs_map_add(interval, "start", has_feed_start && !fs_is_millis_value(feed_start_value)
                                      ? cJSON_Duplicate(feed_start_value, true)
                                      : fs_double(feed_start));
    fs_map_add(interval, "lastSide", fs_str(last_side_value));
    fs_map_add(interval, "lastUpdated", fs_double(now));
    fs_map_add(interval, "leftDuration", fs_double(left));
    fs_map_add(interval, "rightDuration", fs_double(right));
    fs_map_add(interval, "offset", fs_double(offset));
    fs_map_add(interval, "end_offset", fs_double(offset));
    cJSON *start_copy = has_feed_start && !fs_is_millis_value(feed_start_value)
                            ? cJSON_Duplicate(feed_start_value, true)
                            : fs_double(feed_start);
    cJSON *start_copy2 = cJSON_Duplicate(start_copy, true);
    fs_doc_free(&doc);
    if (fs_set(ipath, interval)) {
        /* the library logs and carries on to update the timer; so do we */
        hd_log(TAG, "Failed to create nursing interval entry");
    } else {
        hd_log(TAG, "Created nursing interval entry: %s", id);
    }

    cJSON *last_nursing = fs_map();
    fs_map_add(last_nursing, "mode", fs_str("breast"));
    fs_map_add(last_nursing, "start", start_copy);
    fs_map_add(last_nursing, "duration", fs_double(total));
    fs_map_add(last_nursing, "leftDuration", fs_double(left));
    fs_map_add(last_nursing, "rightDuration", fs_double(right));
    fs_map_add(last_nursing, "offset", fs_double(offset));
    cJSON *last_side_map = fs_map();
    fs_map_add(last_side_map, "start", start_copy2);
    fs_map_add(last_side_map, "lastSide", fs_str(last_side_value));
    fs_update_op_t ops[] = {
        {"timer.active", fs_bool(false)},
        {"timer.paused", fs_bool(true)},
        {"timer.timestamp", timestamp_map(now)},
        {"timer.local_timestamp", fs_double(now)},
        {"timer.lastSide", fs_str(last_side_value)},
        {"timer.leftDuration", NULL},
        {"timer.rightDuration", NULL},
        {"timer.activeSide", NULL},
        {"prefs.lastNursing", last_nursing},
        {"prefs.lastSide", last_side_map},
        {"prefs.timestamp", timestamp_map(now)},
        {"prefs.local_timestamp", fs_double(now)},
    };
    if (fs_update(path, ops, 12))
        return -1;
    hd_log(TAG, "Nursing completed (total %.0fs, L:%.0fs R:%.0fs)", total, left, right);
    return 0;
}

/* -- polling ------------------------------------------------------------ */

typedef struct {
    huck_doc_cb_t cb;
} poll_ctx_t;

static void poll_doc(void *vctx, const char *path, const fs_doc_t *doc)
{
    poll_ctx_t *ctx = vctx;
    for (int i = 0; i < 3; i++) {
        size_t n = strlen(COLLECTIONS[i]);
        if (strncmp(path, COLLECTIONS[i], n) || path[n] != '/')
            continue;
        const char *stamp = doc->exists ? doc->update_time : "missing";
        if (H.polled_once && !strcmp(stamp, H.last_update[i]))
            return;
        snprintf(H.last_update[i], sizeof(H.last_update[i]), "%s", stamp);
        if (doc->exists)
            ctx->cb(COLLECTIONS[i], doc->fields);
        return;
    }
}

int huck_poll(huck_doc_cb_t cb)
{
    char paths[3][96];
    const char *ptrs[3];
    for (int i = 0; i < 3; i++) {
        doc_path(paths[i], sizeof(paths[i]), COLLECTIONS[i], NULL);
        ptrs[i] = paths[i];
    }
    poll_ctx_t ctx = {cb};
    if (fs_batch_get(ptrs, 3, poll_doc, &ctx))
        return -1;
    H.polled_once = true;
    return 0;
}

/* -- daily totals ------------------------------------------------------- */

double huck_day_start(double now, int day_start_hour)
{
    time_t t = (time_t)now;
    struct tm tm;
    localtime_r(&t, &tm);
    tm.tm_hour = day_start_hour;
    tm.tm_min = tm.tm_sec = 0;
    tm.tm_isdst = -1;
    time_t start = mktime(&tm);
    if ((double)start > now) {
        tm.tm_mday -= 1;
        tm.tm_isdst = -1;
        start = mktime(&tm);
    }
    return (double)start;
}

typedef struct {
    double start, end;
    bool feed;
    int count;
    double seconds;
} totals_ctx_t;

static void count_entry(totals_ctx_t *ctx, const cJSON *fields)
{
    double start;
    if (!fs_get_number(fields, "start", &start) || start < ctx->start || start >= ctx->end)
        return;
    const char *mode = fs_get_string(fields, "mode");
    if (ctx->feed) {
        if (!mode || strcmp(mode, "breast"))
            return;
        double l = 0, r = 0;
        fs_get_number(fields, "leftDuration", &l);
        fs_get_number(fields, "rightDuration", &r);
        ctx->seconds += l + r;
    }
    ctx->count++;
}

static void regular_doc(void *vctx, const cJSON *fields)
{
    bool multi = false;
    if (fs_get_bool(fields, "multi", &multi) && multi)
        return; /* multi-entry docs come from the second query */
    count_entry(vctx, fields);
}

static void multi_entry(void *vctx, const cJSON *entry_fields)
{
    count_entry(vctx, entry_fields);
}

static cJSON *field_ref(const char *name)
{
    cJSON *f = cJSON_CreateObject();
    cJSON_AddStringToObject(f, "fieldPath", name);
    return f;
}

static cJSON *field_filter(const char *field, const char *op, cJSON *value)
{
    cJSON *ff = cJSON_CreateObject();
    cJSON *inner = cJSON_AddObjectToObject(ff, "fieldFilter");
    cJSON_AddItemToObject(inner, "field", field_ref(field));
    cJSON_AddStringToObject(inner, "op", op);
    cJSON_AddItemToObject(inner, "value", value);
    return ff;
}

static cJSON *from_intervals(void)
{
    cJSON *from = cJSON_CreateArray();
    cJSON *c = cJSON_CreateObject();
    cJSON_AddStringToObject(c, "collectionId", "intervals");
    cJSON_AddItemToArray(from, c);
    return from;
}

static int count_collection(const char *collection, totals_ctx_t *ctx)
{
    char parent[96];
    doc_path(parent, sizeof(parent), collection, NULL);

    /* Query 1: regular documents with date filtering, ordered by start */
    cJSON *q = cJSON_CreateObject();
    cJSON_AddItemToObject(q, "from", from_intervals());
    cJSON *where = cJSON_AddObjectToObject(q, "where");
    cJSON *comp = cJSON_AddObjectToObject(where, "compositeFilter");
    cJSON_AddStringToObject(comp, "op", "AND");
    cJSON *filters = cJSON_AddArrayToObject(comp, "filters");
    cJSON_AddItemToArray(filters, field_filter("start", "GREATER_THAN_OR_EQUAL", fs_double(ctx->start)));
    cJSON_AddItemToArray(filters, field_filter("start", "LESS_THAN", fs_double(ctx->end)));
    cJSON *order = cJSON_AddArrayToObject(q, "orderBy");
    cJSON *ob = cJSON_CreateObject();
    cJSON_AddItemToObject(ob, "field", field_ref("start"));
    cJSON_AddStringToObject(ob, "direction", "ASCENDING");
    cJSON_AddItemToArray(order, ob);
    /* only the fields the count needs: keeps responses small on a microcontroller */
    cJSON *select = cJSON_AddObjectToObject(q, "select");
    cJSON *sel_fields = cJSON_AddArrayToObject(select, "fields");
    static const char *const WANTED[] = {"start", "mode", "multi", "leftDuration", "rightDuration"};
    for (int i = 0; i < 5; i++)
        cJSON_AddItemToArray(sel_fields, field_ref(WANTED[i]));
    if (fs_run_query(parent, q, regular_doc, ctx))
        return -1;

    /* Query 2: multi-entry documents (can't filter by nested start field). These hold up to
     * 256 entries and run to a few hundred KB, so their `data` map is read an entry at a time. */
    cJSON *q2 = cJSON_CreateObject();
    cJSON_AddItemToObject(q2, "from", from_intervals());
    cJSON_AddItemToObject(q2, "where", field_filter("multi", "EQUAL", fs_bool(true)));
    return fs_run_query_map_entries(parent, q2, "data", multi_entry, ctx);
}

int huck_day_totals(int day_start_hour, day_totals_t *out)
{
    double now = hd_now();
    double start = huck_day_start(now, day_start_hour);
    totals_ctx_t diapers = {start, now, false, 0, 0};
    totals_ctx_t feeds = {start, now, true, 0, 0};
    if (count_collection("diaper", &diapers) || count_collection("feed", &feeds))
        return -1;
    out->day_start = start;
    out->diapers = diapers.count;
    out->nursing_count = feeds.count;
    out->nursing_seconds = feeds.seconds;
    return 0;
}
