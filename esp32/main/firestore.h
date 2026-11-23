/* Firestore over REST, shaped like the writes google-cloud-firestore makes for
 * py-huckleberry-api: set(), set(merge=True), update() with dotted field paths
 * and DELETE_FIELD, get(), batchGet and runQuery.
 *
 * Values are cJSON trees in Firestore's REST "Value" encoding
 * ({"integerValue":"1"}, {"doubleValue":1.5}, {"mapValue":{"fields":{...}}} ...),
 * built with the fs_* constructors so int vs. double lands exactly as the
 * Python client would send it.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "cJSON.h"

/* -- Value constructors ------------------------------------------------- */
cJSON *fs_int(int64_t v);
cJSON *fs_double(double v);
cJSON *fs_bool(bool v);
cJSON *fs_str(const char *s);
cJSON *fs_null(void);
cJSON *fs_map(void);
/* Add `value` under `key` in a map Value (or a bare "fields" object); returns map. */
cJSON *fs_map_add(cJSON *map, const char *key, cJSON *value);
/* The "fields" object inside a map Value. */
cJSON *fs_map_fields(cJSON *map);

/* -- reading Values ----------------------------------------------------- */
/* Look up a Value by dotted path ("timer.active") in a document's fields object. */
const cJSON *fs_get(const cJSON *fields, const char *path);
bool fs_get_number(const cJSON *fields, const char *path, double *out); /* integerValue or doubleValue */
bool fs_get_bool(const cJSON *fields, const char *path, bool *out);
const char *fs_get_string(const cJSON *fields, const char *path);       /* NULL if absent/not a string */
bool fs_is_millis_value(const cJSON *value);                             /* number > 1e11 */

/* -- field paths -------------------------------------------------------- */
/* Quote one segment the way Firestore field paths require (backticks unless a simple identifier). */
void fs_quote_segment(const char *segment, char *out, int n);
/* Every leaf path of a map Value, quoted and dot-joined, written as repeated
 * "updateMask.fieldPaths=" query parameters (URL-encoded). Returns bytes written. */
int fs_merge_mask(const cJSON *map, char *out, int n);

/* -- update ops (ref.update({...})) ------------------------------------- */
typedef struct {
    const char *path;  /* dotted field path as the Python code writes it, e.g. "prefs.lastSleep" */
    cJSON *value;      /* NULL = DELETE_FIELD */
} fs_update_op_t;

/* -- requests ----------------------------------------------------------- */
typedef struct {
    bool exists;
    cJSON *fields;         /* owned; cJSON_Delete when done. NULL if !exists */
    char update_time[40];
} fs_doc_t;

/* doc_path is relative to the database root, e.g. "sleep/abc123". All return 0 on success. */
int fs_get_document(const char *doc_path, fs_doc_t *doc);
void fs_doc_free(fs_doc_t *doc);
/* ref.set(data): replace the whole document. `fields` is a map Value or a fields object; consumed. */
int fs_set(const char *doc_path, cJSON *map);
/* ref.set(data, merge=True): write just the leaves present in `map`; consumed. */
int fs_set_merge(const char *doc_path, cJSON *map);
/* ref.update({...}): dotted paths, DELETE_FIELD, document must exist. Values consumed. */
int fs_update(const char *doc_path, fs_update_op_t *ops, int nops);
/* Fetch several documents in one request; cb gets each (fields NULL when missing). */
typedef void (*fs_doc_cb_t)(void *ctx, const char *doc_path, const fs_doc_t *doc);
int fs_batch_get(const char *const *doc_paths, int n, fs_doc_cb_t cb, void *ctx);
/* Run a structured query under parent_path (e.g. "feed/abc123"); cb gets each document's fields. */
typedef void (*fs_query_cb_t)(void *ctx, const cJSON *fields);
int fs_run_query(const char *parent_path, cJSON *structured_query, fs_query_cb_t cb, void *ctx);

/* Streaming JSON array splitter used by batchGet/runQuery (exposed for tests):
 * feed bytes, get a callback per top-level array element. */
typedef struct {
    char *buf;
    size_t len, cap;
    int depth;
    bool in_string, escape, capturing, overflow;
    void (*on_element)(void *ctx, cJSON *element);
    void *ctx;
} fs_array_splitter_t;
void fs_splitter_init(fs_array_splitter_t *s, void (*on_element)(void *, cJSON *), void *ctx);
void fs_splitter_feed(fs_array_splitter_t *s, const char *data, size_t len);
void fs_splitter_free(fs_array_splitter_t *s);
