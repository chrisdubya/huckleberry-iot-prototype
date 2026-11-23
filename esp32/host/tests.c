/* Host tests for the portable firmware modules: formatting, Firestore
 * encoding, the streaming splitter, and the dispatcher driven end to end
 * through the real huck_api/firestore code against a scripted HTTP mock. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "cJSON.h"
#include "dispatcher.h"
#include "display.h"
#include "firebase_auth.h"
#include "firestore.h"
#include "http.h"
#include "huck_api.h"
#include "platform.h"

extern double host_fake_now, host_fake_uptime;
extern int host_sleep_calls;

static int failures;
#define CHECK(cond) do { if (!(cond)) { failures++; printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); } } while (0)
#define CHECK_STR(a, b) do { const char *_a = (a), *_b = (b); if (!_a || strcmp(_a, _b)) { failures++; printf("FAIL %s:%d: %s = \"%s\", expected \"%s\"\n", __FILE__, __LINE__, #a, _a ? _a : "(null)", _b); } } while (0)

/* -- HTTP mock ----------------------------------------------------------- */

typedef struct {
    char method[8];
    char url[4096];
    char *body;
} request_t;

static request_t requests[64];
static int nrequests;
static int fail_next;        /* respond 500 to this many requests */
static const char *sleep_doc_json;  /* GET sleep/<cid> response */
static const char *feed_doc_json;
static const char *diaper_doc_json;
static const char *batch_json;
static const char *query_json;

static void record(const char *method, const char *url, const char *body)
{
    request_t *r = &requests[nrequests++ % 64];
    snprintf(r->method, sizeof(r->method), "%s", method);
    snprintf(r->url, sizeof(r->url), "%s", url);
    free(r->body);
    r->body = body ? strdup(body) : NULL;
}

static void reset_requests(void)
{
    for (int i = 0; i < 64; i++) {
        free(requests[i].body);
        requests[i].body = NULL;
    }
    nrequests = 0;
}

static request_t *req(int i)
{
    return &requests[i];
}

static const char *canned(const char *method, const char *url)
{
    if (strstr(url, "signInWithPassword"))
        return "{\"idToken\":\"tok\",\"refreshToken\":\"rt\",\"localId\":\"user1\",\"expiresIn\":\"3600\"}";
    if (strstr(url, "securetoken"))
        return "{\"id_token\":\"tok2\",\"refresh_token\":\"rt2\",\"expires_in\":\"3600\"}";
    if (strstr(url, "/users/user1"))
        return "{\"name\":\"x\",\"fields\":{\"childList\":{\"arrayValue\":{\"values\":[{\"mapValue\":{\"fields\":{\"cid\":{\"stringValue\":\"kid1\"},\"nickname\":{\"stringValue\":\"Bean\"}}}}]}}}}";
    if (!strcmp(method, "GET") && strstr(url, "/sleep/kid1"))
        return sleep_doc_json;
    if (!strcmp(method, "GET") && strstr(url, "/feed/kid1"))
        return feed_doc_json;
    if (!strcmp(method, "GET") && strstr(url, "/diaper/kid1"))
        return diaper_doc_json;
    if (strstr(url, ":batchGet"))
        return batch_json;
    if (strstr(url, ":runQuery"))
        return query_json;
    return "{}";
}

int http_request(const char *method, const char *url, const char *bearer, const char *body, http_response_t *resp)
{
    (void)bearer;
    record(method, url, body);
    memset(resp, 0, sizeof(*resp));
    if (fail_next > 0) {
        fail_next--;
        resp->status = 500;
        resp->body = strdup("{\"error\":\"mock\"}");
        resp->len = strlen(resp->body);
        return 0;
    }
    const char *c = canned(method, url);
    if (!c) {
        resp->status = 404;
        resp->body = strdup("{}");
    } else {
        resp->status = 200;
        resp->body = strdup(c);
    }
    resp->len = strlen(resp->body);
    return 0;
}

int http_request_stream(const char *method, const char *url, const char *bearer, const char *body,
                        http_data_cb_t on_data, void *ctx)
{
    (void)bearer;
    record(method, url, body);
    if (fail_next > 0) {
        fail_next--;
        return 500;
    }
    const char *c = canned(method, url);
    if (!c)
        return 404;
    /* deliver in awkward chunks to exercise the splitter */
    size_t len = strlen(c);
    for (size_t i = 0; i < len; i += 7)
        on_data(ctx, c + i, len - i < 7 ? len - i : 7);
    return 200;
}

void http_response_free(http_response_t *resp)
{
    free(resp->body);
    resp->body = NULL;
}

/* -- status capture -------------------------------------------------------- */

static char statuses[2048];

static void on_event(status_t status, action_t action, const char *detail)
{
    static const char *NAMES[] = {"sending", "success", "retrying", "failed", "ignored", "remote"};
    size_t len = strlen(statuses);
    snprintf(statuses + len, sizeof(statuses) - len, "%s:%s;", NAMES[status], action >= 0 ? EVENT_LABELS[action] : "-");
    (void)detail;
}

/* -- tests ---------------------------------------------------------------- */

static void test_formatting(void)
{
    char buf[32];
    format_ago(30, buf, sizeof(buf)); CHECK_STR(buf, "now");
    format_ago(48 * 60, buf, sizeof(buf)); CHECK_STR(buf, "48m");
    format_ago(2 * 3600 + 14 * 60 + 59, buf, sizeof(buf)); CHECK_STR(buf, "2h 14m");
    format_ago(26 * 3600, buf, sizeof(buf)); CHECK_STR(buf, "1d 2h");
    format_ago(-5, buf, sizeof(buf)); CHECK_STR(buf, "now");
    format_timer(462, buf, sizeof(buf)); CHECK_STR(buf, "07:42");
    format_timer(3720, buf, sizeof(buf)); CHECK_STR(buf, "1h 02m");
    format_hours(2 * 3600 + 35 * 60, buf, sizeof(buf)); CHECK_STR(buf, "2h 35m");
    format_hours(59 * 60, buf, sizeof(buf)); CHECK_STR(buf, "59m");
    CHECK(is_millis(1.8e12) && !is_millis(1.8e9));
    CHECK(epoch_seconds(1.8e12) == 1.8e9);
}

static void test_field_paths(void)
{
    char q[64];
    fs_quote_segment("timer", q, sizeof(q)); CHECK_STR(q, "timer");
    fs_quote_segment("10-20_minutes", q, sizeof(q)); CHECK_STR(q, "`10-20_minutes`");
    fs_quote_segment("a`b", q, sizeof(q)); CHECK_STR(q, "`a\\`b`");

    cJSON *inner = fs_map();
    fs_map_add(inner, "happy", fs_bool(false));
    fs_map_add(inner, "10-20_minutes", fs_bool(false));
    cJSON *timer = fs_map();
    fs_map_add(timer, "active", fs_bool(true));
    fs_map_add(timer, "cond", inner);
    fs_map_add(timer, "empty", fs_map());
    cJSON *root = fs_map();
    fs_map_add(root, "timer", timer);
    char mask[1024];
    fs_merge_mask(root, mask, sizeof(mask));
    CHECK_STR(mask, "&updateMask.fieldPaths=timer.active&updateMask.fieldPaths=timer.cond.happy"
                    "&updateMask.fieldPaths=timer.cond.%6010-20_minutes%60&updateMask.fieldPaths=timer.empty");
    cJSON_Delete(root);

    cJSON *i = fs_int(1763856000);
    char *s = cJSON_PrintUnformatted(i);
    CHECK_STR(s, "{\"integerValue\":\"1763856000\"}");
    free(s);
    cJSON_Delete(i);
    cJSON *d = fs_double(300);
    s = cJSON_PrintUnformatted(d);
    CHECK_STR(s, "{\"doubleValue\":300}");
    free(s);
    cJSON_Delete(d);
}

static int elements;
static char last_element[256];

static void on_element(void *ctx, cJSON *el)
{
    (void)ctx;
    elements++;
    char *s = cJSON_PrintUnformatted(el);
    snprintf(last_element, sizeof(last_element), "%s", s);
    free(s);
}

static void test_splitter(void)
{
    const char *json = "[{\"document\":{\"fields\":{\"notes\":{\"stringValue\":\"a } \\\" ] b\"}}},\"readTime\":\"t\"},\n"
                       "{\"readTime\":\"t2\"}]";
    fs_array_splitter_t s;
    elements = 0;
    fs_splitter_init(&s, on_element, NULL);
    for (size_t i = 0; i < strlen(json); i += 3)
        fs_splitter_feed(&s, json + i, strlen(json) - i < 3 ? strlen(json) - i : 3);
    fs_splitter_free(&s);
    CHECK(elements == 2);
    CHECK_STR(last_element, "{\"readTime\":\"t2\"}");

    /* an element over the cap is skipped, the next one still parses */
    size_t big = 100 * 1024;
    char *huge = malloc(big + 64);
    strcpy(huge, "[{\"x\":\"");
    memset(huge + strlen(huge), 'a', big);
    huge[strlen("[{\"x\":\"") + big] = 0;
    strcat(huge, "\"},{\"ok\":1}]");
    elements = 0;
    fs_splitter_init(&s, on_element, NULL);
    fs_splitter_feed(&s, huge, strlen(huge));
    fs_splitter_free(&s);
    free(huge);
    CHECK(elements == 1);
    CHECK_STR(last_element, "{\"ok\":1}");
}

static void setup_deck(void)
{
    setenv("TZ", "EST5EDT,M3.2.0,M11.1.0", 1);
    tzset();
    host_fake_now = 1800000000.0; /* 2027-01-15 03:26:40 EST */
    host_fake_uptime = 1000;
    sleep_doc_json = "{\"name\":\"x\",\"updateTime\":\"s1\",\"fields\":{\"timer\":{\"mapValue\":{\"fields\":{\"active\":{\"booleanValue\":false}}}}}}";
    feed_doc_json = "{\"name\":\"x\",\"updateTime\":\"f1\",\"fields\":{\"timer\":{\"mapValue\":{\"fields\":{\"active\":{\"booleanValue\":false}}}},"
                    "\"prefs\":{\"mapValue\":{\"fields\":{\"lastBottle\":{\"mapValue\":{\"fields\":{\"start\":{\"doubleValue\":1799990000}}}}}}}}}";
    diaper_doc_json = NULL; /* 404: never logged */
    static huck_config_t cfg = {120, "ml", "Formula", "left", ""};
    huck_init(&cfg);
    firebase_auth_init("me@example.com", "pw");
    reset_requests();
    statuses[0] = 0;
    host_sleep_calls = 0;
    CHECK(huck_connect() == 0);
    CHECK_STR(huck_child_uid(), "kid1");
    CHECK_STR(huck_child_name(), "Bean");
    reset_requests();
    hd_state_save(false, false); /* a fresh device: nothing persisted */
    dispatcher_init(2, on_event);
}

static void test_diaper_and_bottle(void)
{
    setup_deck();
    dispatcher_press(BTN_PEE);
    dispatcher_press(BTN_PEE); /* debounced */
    host_fake_uptime += 3;
    dispatcher_press(BTN_BOTTLE);
    dispatcher_process();
    CHECK_STR(statuses, "sending:Pee diaper;ignored:-;sending:Bottle;success:Pee diaper;success:Bottle;");
    /* pee: GET diaper doc (404), PATCH interval, PATCH prefs merge */
    CHECK(nrequests == 6);
    CHECK_STR(req(0)->method, "GET");
    CHECK(strstr(req(0)->url, "/documents/diaper/kid1") != NULL);
    CHECK_STR(req(1)->method, "PATCH");
    CHECK(strstr(req(1)->url, "/diaper/kid1/intervals/1800000000000-") != NULL);
    CHECK(strstr(req(1)->url, "updateMask") == NULL);
    CHECK(strstr(req(1)->body, "\"mode\":{\"stringValue\":\"pee\"}") != NULL);
    CHECK(strstr(req(1)->body, "\"start\":{\"doubleValue\":1800000000}") != NULL);
    CHECK(strstr(req(1)->body, "\"offset\":{\"doubleValue\":300}") != NULL); /* EST = UTC-5 -> +300 */
    CHECK_STR(req(2)->method, "PATCH");
    CHECK(strstr(req(2)->url, "/diaper/kid1?updateMask.fieldPaths=prefs.lastDiaper.start&updateMask.fieldPaths=prefs.lastDiaper.mode"
                              "&updateMask.fieldPaths=prefs.lastDiaper.offset&updateMask.fieldPaths=prefs.timestamp.seconds"
                              "&updateMask.fieldPaths=prefs.local_timestamp") != NULL);
    /* bottle: GET feed doc, PATCH interval, PATCH prefs merge (newer than lastBottle) */
    CHECK(strstr(req(4)->body, "\"bottleType\":{\"stringValue\":\"Formula\"}") != NULL);
    CHECK(strstr(req(4)->body, "\"amount\":{\"doubleValue\":120}") != NULL);
    CHECK(strstr(req(5)->url, "updateMask.fieldPaths=prefs.lastBottle.bottleAmount") != NULL);
    CHECK(strstr(req(5)->body, "\"bottleUnits\":{\"stringValue\":\"ml\"}") != NULL);
}

static void test_sleep_toggle_resolves_against_server(void)
{
    setup_deck();
    /* server says a sleep session is already running (started from the app) */
    sleep_doc_json = "{\"name\":\"x\",\"updateTime\":\"s2\",\"fields\":{\"timer\":{\"mapValue\":{\"fields\":{"
                     "\"active\":{\"booleanValue\":true},\"paused\":{\"booleanValue\":false},"
                     "\"timerStartTime\":{\"doubleValue\":1799999000000},\"uuid\":{\"stringValue\":\"abc\"},"
                     "\"details\":{\"mapValue\":{\"fields\":{\"notes\":{\"stringValue\":\"n\"}}}}}}}}}";
    CHECK(!dispatcher_sleep_active());
    dispatcher_press(BTN_SLEEP_TOGGLE); /* local guess: start */
    CHECK(dispatcher_sleep_active());
    dispatcher_process();
    CHECK_STR(statuses, "sending:Sleep started;sending:Sleep completed;success:Sleep completed;");
    CHECK(!dispatcher_sleep_active()); /* settled to the server's new state */
    /* resolve GET, complete: GET, PATCH interval, PATCH update */
    CHECK(nrequests == 4);
    CHECK(strstr(req(2)->url, "/sleep/kid1/intervals/") != NULL);
    CHECK(strstr(req(2)->body, "\"start\":{\"integerValue\":\"1799999000\"}") != NULL);
    CHECK(strstr(req(2)->body, "\"duration\":{\"integerValue\":\"1000\"}") != NULL);
    CHECK(strstr(req(2)->body, "\"details\":{\"mapValue\":{\"fields\":{\"notes\":{\"stringValue\":\"n\"}}}}") != NULL);
    CHECK(strstr(req(3)->url, "/sleep/kid1?currentDocument.exists=true&updateMask.fieldPaths=timer"
                              "&updateMask.fieldPaths=prefs.lastSleep&updateMask.fieldPaths=prefs.timestamp"
                              "&updateMask.fieldPaths=prefs.local_timestamp") != NULL);
    CHECK(strstr(req(3)->body, "\"timerStartTime\":{\"nullValue\":null}") != NULL);
    CHECK(strstr(req(3)->body, "\"uuid\":{\"stringValue\":\"abc\"}") != NULL);
    CHECK(strstr(req(3)->body, "\"lastSleep\":{\"mapValue\":{\"fields\":{\"start\":{\"integerValue\":\"1799999000\"}") != NULL);
}

static void test_start_sleep_merge_mask(void)
{
    setup_deck();
    dispatcher_press(BTN_SLEEP_TOGGLE);
    dispatcher_process();
    CHECK_STR(statuses, "sending:Sleep started;success:Sleep started;");
    CHECK(nrequests == 2);
    CHECK(strstr(req(1)->url, "updateMask.fieldPaths=timer.details.startSleepCondition.%6010-20_minutes%60") != NULL);
    CHECK(strstr(req(1)->url, "updateMask.fieldPaths=timer.details.sleepLocations.swing") != NULL);
    CHECK(strstr(req(1)->url, "currentDocument") == NULL);
    CHECK(strstr(req(1)->body, "\"timerStartTime\":{\"doubleValue\":1800000000000}") != NULL);
    CHECK(dispatcher_sleep_active());
}

static void test_nursing_complete_from_ios(void)
{
    setup_deck();
    /* nursing started in the iOS app: ms timestamps, 100s banked on the right */
    feed_doc_json = "{\"name\":\"x\",\"updateTime\":\"f2\",\"fields\":{\"timer\":{\"mapValue\":{\"fields\":{"
                    "\"active\":{\"booleanValue\":true},\"paused\":{\"booleanValue\":false},"
                    "\"feedStartTime\":{\"doubleValue\":1799999500000},\"timerStartTime\":{\"doubleValue\":1799999800000},"
                    "\"leftDuration\":{\"integerValue\":\"0\"},\"rightDuration\":{\"doubleValue\":100},"
                    "\"activeSide\":{\"stringValue\":\"left\"},\"uuid\":{\"stringValue\":\"u\"}}}}}}";
    dispatcher_press(BTN_NURSING_TOGGLE);
    dispatcher_process();
    CHECK_STR(statuses, "sending:Nursing started;sending:Nursing completed;success:Nursing completed;");
    /* resolve GET, complete: GET, PATCH ms fix, PATCH interval, PATCH update */
    CHECK(nrequests == 5);
    CHECK(strstr(req(2)->url, "updateMask.fieldPaths=timer.feedStartTime&updateMask.fieldPaths=timer.timerStartTime") != NULL);
    CHECK(strstr(req(2)->body, "\"feedStartTime\":{\"doubleValue\":1799999500}") != NULL);
    CHECK(strstr(req(3)->body, "\"start\":{\"doubleValue\":1799999500}") != NULL);
    CHECK(strstr(req(3)->body, "\"leftDuration\":{\"doubleValue\":200}") != NULL);
    CHECK(strstr(req(3)->body, "\"rightDuration\":{\"doubleValue\":100}") != NULL);
    CHECK(strstr(req(3)->body, "\"lastSide\":{\"stringValue\":\"left\"}") != NULL);
    CHECK(strstr(req(4)->url, "updateMask.fieldPaths=timer.leftDuration&updateMask.fieldPaths=timer.rightDuration"
                              "&updateMask.fieldPaths=timer.activeSide") != NULL);
    CHECK(strstr(req(4)->body, "leftDuration") != NULL && strstr(req(4)->body, "\"activeSide\"") == NULL);
    CHECK(strstr(req(4)->body, "\"duration\":{\"doubleValue\":300}") != NULL);
}

static void test_retry_and_give_up(void)
{
    setup_deck();
    fail_next = 2;
    dispatcher_press(BTN_POO);
    dispatcher_process();
    CHECK_STR(statuses, "sending:Poop diaper;retrying:Poop diaper;retrying:Poop diaper;success:Poop diaper;");
    CHECK(host_sleep_calls == 2);

    statuses[0] = 0;
    fail_next = 3;
    dispatcher_press(BTN_SLEEP_TOGGLE);
    CHECK(dispatcher_sleep_active()); /* optimistic */
    dispatcher_process();
    CHECK_STR(statuses, "sending:Sleep started;retrying:Sleep started;retrying:Sleep started;failed:Sleep started;");
    CHECK(!dispatcher_sleep_active()); /* guess dropped */
}

static void test_sync_remote(void)
{
    setup_deck();
    dispatcher_sync_remote(KIND_NURSING, true);
    CHECK(dispatcher_nursing_active());
    CHECK_STR(statuses, "remote:Nursing started;");
    dispatcher_sync_remote(KIND_NURSING, true); /* echo: no event */
    CHECK_STR(statuses, "remote:Nursing started;");
}

static int polled;
static char polled_names[64];

static void poll_cb(const char *collection, const cJSON *fields)
{
    (void)fields;
    polled++;
    strcat(polled_names, collection);
    strcat(polled_names, ",");
}

static void test_poll_change_detection(void)
{
    setup_deck();
    batch_json = "[{\"found\":{\"name\":\"projects/p/databases/(default)/documents/sleep/kid1\",\"updateTime\":\"a\",\"fields\":{}},\"readTime\":\"r\"},"
                 "{\"found\":{\"name\":\"projects/p/databases/(default)/documents/feed/kid1\",\"updateTime\":\"b\",\"fields\":{}},\"readTime\":\"r\"},"
                 "{\"missing\":\"projects/p/databases/(default)/documents/diaper/kid1\",\"readTime\":\"r\"}]";
    polled = 0;
    polled_names[0] = 0;
    CHECK(huck_poll(poll_cb) == 0);
    CHECK(polled == 2);
    CHECK_STR(polled_names, "sleep,feed,");
    CHECK(strstr(req(0)->body, "\"projects/simpleintervals/databases/(default)/documents/sleep/kid1\"") != NULL);
    CHECK(huck_poll(poll_cb) == 0);
    CHECK(polled == 2); /* nothing changed */
    batch_json = "[{\"found\":{\"name\":\"projects/p/databases/(default)/documents/sleep/kid1\",\"updateTime\":\"a2\",\"fields\":{}},\"readTime\":\"r\"},"
                 "{\"found\":{\"name\":\"projects/p/databases/(default)/documents/feed/kid1\",\"updateTime\":\"b\",\"fields\":{}},\"readTime\":\"r\"},"
                 "{\"missing\":\"projects/p/databases/(default)/documents/diaper/kid1\",\"readTime\":\"r\"}]";
    CHECK(huck_poll(poll_cb) == 0);
    CHECK(polled == 3);
}

static void test_day_totals(void)
{
    setup_deck();
    /* 03:26 EST: the day started yesterday 08:00 EST = 1799930000 - check against 8am */
    double start = huck_day_start(host_fake_now, 8);
    time_t t = (time_t)start;
    struct tm tm;
    localtime_r(&t, &tm);
    CHECK(tm.tm_hour == 8 && tm.tm_min == 0);
    CHECK(host_fake_now - start < 86400 && host_fake_now > start);
    query_json = "[{\"document\":{\"fields\":{\"start\":{\"doubleValue\":1799990000},\"mode\":{\"stringValue\":\"breast\"},"
                 "\"leftDuration\":{\"doubleValue\":600},\"rightDuration\":{\"integerValue\":\"120\"}}}},"
                 "{\"document\":{\"fields\":{\"start\":{\"doubleValue\":1799991000},\"mode\":{\"stringValue\":\"bottle\"}}}},"
                 "{\"document\":{\"fields\":{\"multi\":{\"booleanValue\":true},\"data\":{\"mapValue\":{\"fields\":{"
                 "\"e1\":{\"mapValue\":{\"fields\":{\"start\":{\"doubleValue\":1799992000},\"mode\":{\"stringValue\":\"breast\"},\"leftDuration\":{\"doubleValue\":60}}}},"
                 "\"e2\":{\"mapValue\":{\"fields\":{\"start\":{\"doubleValue\":1700000000},\"mode\":{\"stringValue\":\"breast\"}}}}}}}}}},"
                 "{\"readTime\":\"r\"}]";
    day_totals_t totals;
    CHECK(huck_day_totals(8, &totals) == 0);
    /* the same canned response serves both collections: diapers count every regular + in-range multi entry */
    CHECK(totals.diapers == 3);
    CHECK(totals.nursing_count == 2);
    CHECK(totals.nursing_seconds == 780);
    CHECK(strstr(req(0)->url, "/feed/kid1:runQuery") != NULL || strstr(req(0)->url, "/diaper/kid1:runQuery") != NULL);
    CHECK(strstr(req(0)->body, "\"op\":\"GREATER_THAN_OR_EQUAL\",\"value\":{\"doubleValue\":") != NULL);
    CHECK(strstr(req(1)->body, "\"fieldPath\":\"multi\"},\"op\":\"EQUAL\",\"value\":{\"booleanValue\":true}") != NULL);
}

static void test_display_docs(void)
{
    display_config_t cfg = {DISPLAY_MODE_TICKER, 80, 20, 60, 34};
    display_init(&cfg, 1800000000.0);
    cJSON *feed = cJSON_Parse("{\"timer\":{\"mapValue\":{\"fields\":{\"active\":{\"booleanValue\":true},\"paused\":{\"booleanValue\":false},"
                              "\"timerStartTime\":{\"doubleValue\":1799999800000},\"leftDuration\":{\"doubleValue\":100},"
                              "\"activeSide\":{\"stringValue\":\"right\"}}}},"
                              "\"prefs\":{\"mapValue\":{\"fields\":{\"lastNursing\":{\"mapValue\":{\"fields\":{\"start\":{\"doubleValue\":1799990000},\"duration\":{\"doubleValue\":750}}}},"
                              "\"lastSide\":{\"mapValue\":{\"fields\":{\"lastSide\":{\"stringValue\":\"left\"}}}}}}}}");
    feed_doc_t fd;
    huck_parse_feed(feed, &fd);
    display_on_feed_doc(&fd);
    deck_state_t *s = display_state();
    CHECK(s->nursing_active && !s->nursing_paused);
    CHECK_STR(s->nursing_side, "right");
    CHECK(s->nursing_segment_start == 1799999800.0); /* ms -> s */
    CHECK_STR(s->last_feed_detail, "12m L");         /* round(750/60) = 12 (half to even) */
    cJSON_Delete(feed);
}

int main(void)
{
    test_formatting();
    test_field_paths();
    test_splitter();
    test_diaper_and_bottle();
    test_sleep_toggle_resolves_against_server();
    test_start_sleep_merge_mask();
    test_nursing_complete_from_ios();
    test_retry_and_give_up();
    test_sync_remote();
    test_poll_change_detection();
    test_day_totals();
    test_display_docs();
    if (failures) {
        printf("%d failure(s)\n", failures);
        return 1;
    }
    printf("All tests passed\n");
    return 0;
}
