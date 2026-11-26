#include "firestore.h"

#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "firebase_auth.h"
#include "http.h"
#include "platform.h"

static const char *TAG = "firestore";

#define MAX_ELEMENT_BYTES (96 * 1024) /* one runQuery/batchGet element; bigger ones are skipped */

/* -- Value constructors ------------------------------------------------- */

cJSON *fs_int(int64_t v)
{
    char buf[24];
    snprintf(buf, sizeof(buf), "%" PRId64, v);
    cJSON *o = cJSON_CreateObject();
    cJSON_AddStringToObject(o, "integerValue", buf);
    return o;
}

cJSON *fs_double(double v)
{
    cJSON *o = cJSON_CreateObject();
    cJSON_AddNumberToObject(o, "doubleValue", v);
    return o;
}

cJSON *fs_bool(bool v)
{
    cJSON *o = cJSON_CreateObject();
    cJSON_AddBoolToObject(o, "booleanValue", v);
    return o;
}

cJSON *fs_str(const char *s)
{
    cJSON *o = cJSON_CreateObject();
    cJSON_AddStringToObject(o, "stringValue", s);
    return o;
}

cJSON *fs_null(void)
{
    cJSON *o = cJSON_CreateObject();
    cJSON_AddNullToObject(o, "nullValue");
    return o;
}

cJSON *fs_map(void)
{
    cJSON *o = cJSON_CreateObject();
    cJSON *mv = cJSON_AddObjectToObject(o, "mapValue");
    cJSON_AddObjectToObject(mv, "fields");
    return o;
}

cJSON *fs_map_fields(cJSON *map)
{
    cJSON *mv = cJSON_GetObjectItemCaseSensitive(map, "mapValue");
    if (!mv)
        return map; /* a bare fields object */
    return cJSON_GetObjectItemCaseSensitive(mv, "fields");
}

cJSON *fs_map_add(cJSON *map, const char *key, cJSON *value)
{
    cJSON *fields = fs_map_fields(map);
    cJSON_DeleteItemFromObjectCaseSensitive(fields, key);
    cJSON_AddItemToObject(fields, key, value);
    return map;
}

/* -- reading ------------------------------------------------------------ */

const cJSON *fs_get(const cJSON *fields, const char *path)
{
    const cJSON *cur = fields;
    char seg[64];
    while (cur && *path) {
        const char *dot = strchr(path, '.');
        size_t n = dot ? (size_t)(dot - path) : strlen(path);
        if (n >= sizeof(seg))
            return NULL;
        memcpy(seg, path, n);
        seg[n] = 0;
        cur = cJSON_GetObjectItemCaseSensitive(cur, seg);
        if (!cur)
            return NULL;
        if (dot) {
            const cJSON *mv = cJSON_GetObjectItemCaseSensitive(cur, "mapValue");
            cur = mv ? cJSON_GetObjectItemCaseSensitive(mv, "fields") : NULL;
            path = dot + 1;
        } else {
            path += n;
        }
    }
    return cur;
}

static bool value_number(const cJSON *v, double *out)
{
    if (!v)
        return false;
    const cJSON *iv = cJSON_GetObjectItemCaseSensitive(v, "integerValue");
    if (cJSON_IsString(iv)) {
        *out = strtod(iv->valuestring, NULL);
        return true;
    }
    const cJSON *dv = cJSON_GetObjectItemCaseSensitive(v, "doubleValue");
    if (cJSON_IsNumber(dv)) {
        *out = dv->valuedouble;
        return true;
    }
    return false;
}

bool fs_get_number(const cJSON *fields, const char *path, double *out)
{
    return value_number(fs_get(fields, path), out);
}

bool fs_get_bool(const cJSON *fields, const char *path, bool *out)
{
    const cJSON *v = fs_get(fields, path);
    const cJSON *bv = v ? cJSON_GetObjectItemCaseSensitive(v, "booleanValue") : NULL;
    if (!cJSON_IsBool(bv))
        return false;
    *out = cJSON_IsTrue(bv);
    return true;
}

const char *fs_get_string(const cJSON *fields, const char *path)
{
    const cJSON *v = fs_get(fields, path);
    const cJSON *sv = v ? cJSON_GetObjectItemCaseSensitive(v, "stringValue") : NULL;
    return cJSON_IsString(sv) ? sv->valuestring : NULL;
}

bool fs_is_millis_value(const cJSON *value)
{
    double d;
    return value_number(value, &d) && d > 1e11;
}

/* -- field paths -------------------------------------------------------- */

static bool simple_identifier(const char *s)
{
    if (!*s)
        return false;
    if (!((*s >= 'A' && *s <= 'Z') || (*s >= 'a' && *s <= 'z') || *s == '_'))
        return false;
    for (; *s; s++)
        if (!((*s >= 'A' && *s <= 'Z') || (*s >= 'a' && *s <= 'z') || (*s >= '0' && *s <= '9') || *s == '_'))
            return false;
    return true;
}

void fs_quote_segment(const char *segment, char *out, int n)
{
    if (simple_identifier(segment)) {
        snprintf(out, n, "%s", segment);
        return;
    }
    int i = 0;
    if (i < n - 1)
        out[i++] = '`';
    for (const char *p = segment; *p && i < n - 3; p++) {
        if (*p == '`' || *p == '\\')
            out[i++] = '\\';
        out[i++] = *p;
    }
    if (i < n - 1)
        out[i++] = '`';
    out[i] = 0;
}

static int url_encode(const char *s, char *out, int n)
{
    static const char *hex = "0123456789ABCDEF";
    int i = 0;
    for (; *s && i < n - 4; s++) {
        unsigned char c = (unsigned char)*s;
        if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.' || c == '~') {
            out[i++] = c;
        } else {
            out[i++] = '%';
            out[i++] = hex[c >> 4];
            out[i++] = hex[c & 15];
        }
    }
    out[i] = 0;
    return i;
}

static int append_mask_param(char *out, int n, int pos, const char *path)
{
    char enc[256];
    url_encode(path, enc, sizeof(enc));
    return pos + snprintf(out + pos, n > pos ? n - pos : 0, "&updateMask.fieldPaths=%s", enc);
}

static int leaf_paths(const cJSON *fields, const char *prefix, char *out, int n, int pos)
{
    for (const cJSON *f = fields->child; f; f = f->next) {
        char seg[96], path[256];
        fs_quote_segment(f->string, seg, sizeof(seg));
        snprintf(path, sizeof(path), "%s%s%s", prefix, *prefix ? "." : "", seg);
        const cJSON *mv = cJSON_GetObjectItemCaseSensitive(f, "mapValue");
        const cJSON *sub = mv ? cJSON_GetObjectItemCaseSensitive(mv, "fields") : NULL;
        if (sub && sub->child) {
            pos = leaf_paths(sub, path, out, n, pos);
        } else {
            pos = append_mask_param(out, n, pos, path);
        }
    }
    return pos;
}

int fs_merge_mask(const cJSON *map, char *out, int n)
{
    out[0] = 0;
    const cJSON *fields = fs_map_fields((cJSON *)map);
    return leaf_paths(fields, "", out, n, 0);
}

/* Quote a dotted path written in Python style ("prefs.lastSleep") segment by segment. */
static void quote_dotted(const char *dotted, char *out, int n)
{
    int pos = 0;
    out[0] = 0;
    while (*dotted) {
        const char *dot = strchr(dotted, '.');
        size_t len = dot ? (size_t)(dot - dotted) : strlen(dotted);
        char seg[96], q[128];
        if (len >= sizeof(seg))
            len = sizeof(seg) - 1;
        memcpy(seg, dotted, len);
        seg[len] = 0;
        fs_quote_segment(seg, q, sizeof(q));
        pos += snprintf(out + pos, n - pos, "%s%s", pos ? "." : "", q);
        if (!dot)
            break;
        dotted = dot + 1;
    }
}

/* -- JSON array splitter ----------------------------------------------- */

void fs_splitter_init(fs_array_splitter_t *s, void (*on_element)(void *, cJSON *), void *ctx)
{
    memset(s, 0, sizeof(*s));
    s->on_element = on_element;
    s->ctx = ctx;
    s->capture_depth = 2; /* [ element */
}

/* In a runQuery response an entry of a top-level map field sits at
 * [ element { document { fields { <field> { mapValue { fields { <key>: { ... */
#define FIELDS_DEPTH 4
#define MAP_ENTRY_DEPTH 8

void fs_splitter_init_map_entries(fs_array_splitter_t *s, const char *map_field, void (*on_entry)(void *, cJSON *), void *ctx)
{
    fs_splitter_init(s, on_entry, ctx);
    if (map_field) {
        s->capture_depth = MAP_ENTRY_DEPTH;
        s->map_field = map_field;
    }
}

static void splitter_push(fs_array_splitter_t *s, char c)
{
    if (s->overflow)
        return;
    if (s->len + 2 > s->cap) {
        size_t cap = s->cap ? s->cap * 2 : 1024;
        if (cap > MAX_ELEMENT_BYTES) {
            s->overflow = true;
            return;
        }
        char *nb = realloc(s->buf, cap);
        if (!nb) {
            s->overflow = true;
            return;
        }
        s->buf = nb;
        s->cap = cap;
    }
    s->buf[s->len++] = c;
}

void fs_splitter_feed(fs_array_splitter_t *s, const char *data, size_t len)
{
    for (size_t i = 0; i < len; i++) {
        char c = data[i];
        if (s->capturing)
            splitter_push(s, c);
        if (s->in_string) {
            if (s->escape)
                s->escape = false;
            else if (c == '\\')
                s->escape = true;
            else if (c == '"')
                s->in_string = false;
            if (s->map_field && s->depth == FIELDS_DEPTH) {
                /* strings directly inside a document's "fields" are its field names */
                if (!s->in_string)
                    s->key_match = s->key_len >= 0 && !strncmp(s->key, s->map_field, s->key_len) && !s->map_field[s->key_len];
                else if (s->key_len >= 0 && s->key_len < (int)sizeof(s->key))
                    s->key[s->key_len++] = c;
                else
                    s->key_len = -1; /* longer than any name we look for */
            }
            continue;
        }
        if (c == '"') {
            s->in_string = true;
            if (s->map_field && s->depth == FIELDS_DEPTH) {
                s->key_len = 0;
                s->key_match = false;
            }
        } else if (c == '{' || c == '[') {
            s->depth++;
            if (s->depth == s->capture_depth && c == '{' && !s->capturing && (!s->map_field || s->key_match)) {
                /* an element of the top-level array (or an entry of the map field) starts */
                s->capturing = true;
                s->len = 0;
                s->overflow = false;
                splitter_push(s, c);
            }
        } else if (c == '}' || c == ']') {
            s->depth--;
            if (s->depth == s->capture_depth - 1 && s->capturing) {
                s->capturing = false;
                if (s->overflow) {
                    hd_log(TAG, "Skipping a response element over %d bytes", MAX_ELEMENT_BYTES);
                } else {
                    s->buf[s->len] = 0;
                    cJSON *el = cJSON_ParseWithLength(s->buf, s->len);
                    if (el)
                        s->on_element(s->ctx, el);
                    else
                        hd_log(TAG, "Bad JSON element in response");
                    cJSON_Delete(el);
                }
                s->len = 0;
            }
        }
    }
}

void fs_splitter_free(fs_array_splitter_t *s)
{
    free(s->buf);
    s->buf = NULL;
    s->len = s->cap = 0;
}

/* -- requests ----------------------------------------------------------- */

static int build_url(char *url, int n, const char *doc_path, const char *suffix)
{
    return snprintf(url, n, FIRESTORE_BASE_URL "/%s%s", doc_path, suffix ? suffix : "");
}

/* Run a buffered request with a fresh token; one retry after re-signing in on 401. */
static int authed_request(const char *method, const char *url, const char *body, http_response_t *resp)
{
    int result = -1;
    hd_net_lock(); /* the token can't be refreshed out from under us mid-request */
    for (int attempt = 0; attempt < 2; attempt++) {
        if (firebase_auth_ensure())
            break;
        memset(resp, 0, sizeof(*resp));
        int rc = http_request(method, url, firebase_auth_id_token(), body, resp);
        if (rc < 0)
            break;
        if (resp->status == 401 && attempt == 0) {
            http_response_free(resp);
            hd_log(TAG, "401 from Firestore; signing in again");
            if (firebase_auth_signin())
                break;
            continue;
        }
        result = 0;
        break;
    }
    hd_net_unlock();
    return result;
}

static void parse_document(const cJSON *document, fs_doc_t *doc)
{
    memset(doc, 0, sizeof(*doc));
    if (!document)
        return;
    doc->exists = true;
    const cJSON *fields = cJSON_GetObjectItemCaseSensitive(document, "fields");
    doc->fields = fields ? cJSON_Duplicate(fields, true) : cJSON_CreateObject();
    const cJSON *ut = cJSON_GetObjectItemCaseSensitive(document, "updateTime");
    if (cJSON_IsString(ut))
        snprintf(doc->update_time, sizeof(doc->update_time), "%s", ut->valuestring);
}

void fs_doc_free(fs_doc_t *doc)
{
    cJSON_Delete(doc->fields);
    doc->fields = NULL;
    doc->exists = false;
}

int fs_get_document(const char *doc_path, fs_doc_t *doc)
{
    char url[256];
    build_url(url, sizeof(url), doc_path, NULL);
    http_response_t resp;
    memset(doc, 0, sizeof(*doc));
    if (authed_request("GET", url, NULL, &resp))
        return -1;
    if (resp.status == 404) {
        http_response_free(&resp);
        return 0; /* doc->exists stays false */
    }
    if (resp.status != 200) {
        hd_log(TAG, "GET %s failed: HTTP %d %.200s", doc_path, resp.status, resp.body ? resp.body : "");
        http_response_free(&resp);
        return -1;
    }
    cJSON *document = cJSON_Parse(resp.body);
    http_response_free(&resp);
    if (!document)
        return -1;
    parse_document(document, doc);
    cJSON_Delete(document);
    return 0;
}

static int patch(const char *doc_path, const char *query, cJSON *fields_obj)
{
    char url[3072];
    build_url(url, sizeof(url), doc_path, query);
    cJSON *body_obj = cJSON_CreateObject();
    cJSON_AddItemToObject(body_obj, "fields", fields_obj);
    char *body = cJSON_PrintUnformatted(body_obj);
    cJSON_Delete(body_obj);
    http_response_t resp;
    int rc = authed_request("PATCH", url, body, &resp);
    free(body);
    if (rc)
        return -1;
    if (resp.status != 200) {
        hd_log(TAG, "PATCH %s failed: HTTP %d %.200s", doc_path, resp.status, resp.body ? resp.body : "");
        http_response_free(&resp);
        return -1;
    }
    http_response_free(&resp);
    return 0;
}

/* Detach the fields object from a map Value (or pass a bare fields object through). */
static cJSON *take_fields(cJSON *map)
{
    cJSON *mv = cJSON_GetObjectItemCaseSensitive(map, "mapValue");
    if (!mv)
        return map;
    cJSON *fields = cJSON_DetachItemFromObjectCaseSensitive(mv, "fields");
    cJSON_Delete(map);
    return fields;
}

int fs_set(const char *doc_path, cJSON *map)
{
    return patch(doc_path, NULL, take_fields(map));
}

int fs_set_merge(const char *doc_path, cJSON *map)
{
    char mask[2560];
    fs_merge_mask(map, mask, sizeof(mask));
    char query[2564] = "";
    if (mask[0])
        snprintf(query, sizeof(query), "?%s", mask + 1); /* drop the leading '&' */
    return patch(doc_path, query, take_fields(map));
}

/* Insert `value` at a dotted path inside a fields object, creating map Values on the way. */
static void insert_at_path(cJSON *fields, const char *dotted, cJSON *value)
{
    char seg[96];
    const char *dot = strchr(dotted, '.');
    size_t len = dot ? (size_t)(dot - dotted) : strlen(dotted);
    if (len >= sizeof(seg))
        len = sizeof(seg) - 1;
    memcpy(seg, dotted, len);
    seg[len] = 0;
    if (!dot) {
        cJSON_DeleteItemFromObjectCaseSensitive(fields, seg);
        cJSON_AddItemToObject(fields, seg, value);
        return;
    }
    cJSON *child = cJSON_GetObjectItemCaseSensitive(fields, seg);
    if (!child) {
        child = fs_map();
        cJSON_AddItemToObject(fields, seg, child);
    }
    insert_at_path(fs_map_fields(child), dot + 1, value);
}

int fs_update(const char *doc_path, fs_update_op_t *ops, int nops)
{
    char query[2048] = "?currentDocument.exists=true";
    int pos = (int)strlen(query);
    cJSON *fields = cJSON_CreateObject();
    for (int i = 0; i < nops; i++) {
        char quoted[256];
        quote_dotted(ops[i].path, quoted, sizeof(quoted));
        pos = append_mask_param(query, sizeof(query), pos, quoted);
        if (ops[i].value)
            insert_at_path(fields, ops[i].path, ops[i].value);
    }
    return patch(doc_path, query, fields);
}

typedef struct {
    fs_doc_cb_t cb;
    void *ctx;
} batch_ctx_t;

static const char *strip_db_prefix(const char *name)
{
    const char *marker = "/documents/";
    const char *p = strstr(name, marker);
    return p ? p + strlen(marker) : name;
}

static void batch_element(void *vctx, cJSON *el)
{
    batch_ctx_t *ctx = vctx;
    const cJSON *found = cJSON_GetObjectItemCaseSensitive(el, "found");
    const cJSON *missing = cJSON_GetObjectItemCaseSensitive(el, "missing");
    fs_doc_t doc;
    if (found) {
        parse_document(found, &doc);
        const cJSON *name = cJSON_GetObjectItemCaseSensitive(found, "name");
        ctx->cb(ctx->ctx, cJSON_IsString(name) ? strip_db_prefix(name->valuestring) : "", &doc);
        fs_doc_free(&doc);
    } else if (cJSON_IsString(missing)) {
        memset(&doc, 0, sizeof(doc));
        ctx->cb(ctx->ctx, strip_db_prefix(missing->valuestring), &doc);
    }
}

static void splitter_data(void *ctx, const char *data, size_t len)
{
    fs_splitter_feed(ctx, data, len);
}

/* map_field NULL: on_element gets each top-level array element; otherwise each entry of that map field. */
static int streamed_post(const char *url, const char *body, const char *map_field, void (*on_element)(void *, cJSON *), void *ctx)
{
    fs_array_splitter_t splitter;
    fs_splitter_init_map_entries(&splitter, map_field, on_element, ctx);
    int status = -1;
    hd_net_lock();
    for (int attempt = 0; attempt < 2; attempt++) {
        if (firebase_auth_ensure())
            break;
        status = http_request_stream("POST", url, firebase_auth_id_token(), body, splitter_data, &splitter);
        if (status == 401 && attempt == 0) {
            hd_log(TAG, "401 from Firestore; signing in again");
            if (firebase_auth_signin())
                break;
            fs_splitter_free(&splitter);
            fs_splitter_init_map_entries(&splitter, map_field, on_element, ctx);
            continue;
        }
        break;
    }
    hd_net_unlock();
    fs_splitter_free(&splitter);
    if (status != 200) {
        hd_log(TAG, "POST %s failed: HTTP %d", url, status);
        return -1;
    }
    return 0;
}

int fs_batch_get(const char *const *doc_paths, int n, fs_doc_cb_t cb, void *ctx)
{
    cJSON *req = cJSON_CreateObject();
    cJSON *docs = cJSON_AddArrayToObject(req, "documents");
    for (int i = 0; i < n; i++) {
        char name[256];
        snprintf(name, sizeof(name), "projects/" FIREBASE_PROJECT_ID "/databases/(default)/documents/%s", doc_paths[i]);
        cJSON_AddItemToArray(docs, cJSON_CreateString(name));
    }
    char *body = cJSON_PrintUnformatted(req);
    cJSON_Delete(req);
    batch_ctx_t bctx = {cb, ctx};
    int rc = streamed_post(FIRESTORE_BASE_URL ":batchGet", body, NULL, batch_element, &bctx);
    free(body);
    return rc;
}

typedef struct {
    fs_query_cb_t cb;
    void *ctx;
} query_ctx_t;

static void query_element(void *vctx, cJSON *el)
{
    query_ctx_t *ctx = vctx;
    const cJSON *document = cJSON_GetObjectItemCaseSensitive(el, "document");
    const cJSON *fields = document ? cJSON_GetObjectItemCaseSensitive(document, "fields") : NULL;
    if (fields)
        ctx->cb(ctx->ctx, fields);
}

static void query_map_entry(void *vctx, cJSON *entry)
{
    query_ctx_t *ctx = vctx;
    const cJSON *mv = cJSON_GetObjectItemCaseSensitive(entry, "mapValue");
    const cJSON *fields = mv ? cJSON_GetObjectItemCaseSensitive(mv, "fields") : NULL;
    if (fields)
        ctx->cb(ctx->ctx, fields);
}

static int run_query(const char *parent_path, cJSON *structured_query, const char *map_field, fs_query_cb_t cb, void *ctx)
{
    char url[256];
    build_url(url, sizeof(url), parent_path, ":runQuery");
    cJSON *req = cJSON_CreateObject();
    cJSON_AddItemToObject(req, "structuredQuery", structured_query);
    char *body = cJSON_PrintUnformatted(req);
    cJSON_Delete(req);
    query_ctx_t qctx = {cb, ctx};
    int rc = streamed_post(url, body, map_field, map_field ? query_map_entry : query_element, &qctx);
    free(body);
    return rc;
}

int fs_run_query(const char *parent_path, cJSON *structured_query, fs_query_cb_t cb, void *ctx)
{
    return run_query(parent_path, structured_query, NULL, cb, ctx);
}

int fs_run_query_map_entries(const char *parent_path, cJSON *structured_query, const char *map_field, fs_query_cb_t cb, void *ctx)
{
    return run_query(parent_path, structured_query, map_field, cb, ctx);
}
