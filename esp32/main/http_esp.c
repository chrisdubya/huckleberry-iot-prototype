/* http.h on esp_http_client with the built-in CA bundle. One persistent,
 * keep-alive client is reused for Firestore so a button press doesn't pay
 * for a TLS handshake per request; everything is serialized by a mutex. */
#include <stdlib.h>
#include <string.h>

#include "esp_crt_bundle.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "http.h"

static const char *TAG = "http";

#define MAX_BODY (64 * 1024)
#define TIMEOUT_MS 20000

static SemaphoreHandle_t lock;
static esp_http_client_handle_t client;
static char client_host[64];

static esp_http_client_method_t method_of(const char *method)
{
    if (!strcmp(method, "POST")) return HTTP_METHOD_POST;
    if (!strcmp(method, "PATCH")) return HTTP_METHOD_PATCH;
    if (!strcmp(method, "DELETE")) return HTTP_METHOD_DELETE;
    return HTTP_METHOD_GET;
}

static void host_of(const char *url, char *out, int n)
{
    const char *p = strstr(url, "://");
    p = p ? p + 3 : url;
    const char *end = strpbrk(p, "/?");
    int len = end ? (int)(end - p) : (int)strlen(p);
    if (len >= n)
        len = n - 1;
    memcpy(out, p, len);
    out[len] = 0;
}

static esp_http_client_handle_t get_client(const char *url)
{
    char host[64];
    host_of(url, host, sizeof(host));
    if (client && strcmp(host, client_host)) {
        esp_http_client_cleanup(client);
        client = NULL;
    }
    if (!client) {
        esp_http_client_config_t cfg = {
            .url = url,
            .timeout_ms = TIMEOUT_MS,
            .crt_bundle_attach = esp_crt_bundle_attach,
            .buffer_size = 2048,
            .buffer_size_tx = 6144, /* merge masks make long URLs, and the bearer token is ~1 KB */
            .keep_alive_enable = true,
        };
        client = esp_http_client_init(&cfg);
        snprintf(client_host, sizeof(client_host), "%s", host);
    }
    return client;
}

typedef struct {
    http_data_cb_t on_data;
    void *ctx;
    http_response_t *resp;
    bool overflow;
} sink_t;

static void sink(sink_t *s, const char *data, int len)
{
    if (s->on_data) {
        s->on_data(s->ctx, data, len);
        return;
    }
    http_response_t *r = s->resp;
    if (s->overflow || r->len + len + 1 > MAX_BODY) {
        s->overflow = true;
        return;
    }
    char *nb = realloc(r->body, r->len + len + 1);
    if (!nb) {
        s->overflow = true;
        return;
    }
    r->body = nb;
    memcpy(r->body + r->len, data, len);
    r->len += len;
    r->body[r->len] = 0;
}

/* Returns the HTTP status, or <0. */
static int do_request(const char *method, const char *url, const char *bearer, const char *body, sink_t *s)
{
    if (!lock)
        lock = xSemaphoreCreateMutex();
    xSemaphoreTake(lock, portMAX_DELAY);
    int status = -1;
    for (int attempt = 0; attempt < 2; attempt++) {
        esp_http_client_handle_t c = get_client(url);
        if (!c)
            break;
        esp_http_client_set_url(c, url);
        esp_http_client_set_method(c, method_of(method));
        esp_http_client_set_header(c, "Content-Type", "application/json");
        char auth[2048];
        if (bearer) {
            snprintf(auth, sizeof(auth), "Bearer %s", bearer);
            esp_http_client_set_header(c, "Authorization", auth);
        } else {
            esp_http_client_delete_header(c, "Authorization");
        }
        int body_len = body ? (int)strlen(body) : 0;
        esp_err_t err = esp_http_client_open(c, body_len);
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "open %s failed: %s (attempt %d)", url, esp_err_to_name(err), attempt + 1);
            esp_http_client_cleanup(client); /* a stale keep-alive connection: start over */
            client = NULL;
            continue;
        }
        if (body_len && esp_http_client_write(c, body, body_len) != body_len) {
            ESP_LOGW(TAG, "write failed");
            esp_http_client_close(c);
            continue;
        }
        int64_t content_length = esp_http_client_fetch_headers(c);
        if (content_length < 0) {
            ESP_LOGW(TAG, "fetch_headers failed");
            esp_http_client_close(c);
            continue;
        }
        status = esp_http_client_get_status_code(c);
        char chunk[1024];
        for (;;) {
            int n = esp_http_client_read(c, chunk, sizeof(chunk));
            if (n < 0) {
                ESP_LOGW(TAG, "read failed");
                status = -1;
                break;
            }
            if (n == 0)
                break;
            sink(s, chunk, n);
        }
        if (status < 0 || !esp_http_client_is_complete_data_received(c)) {
            esp_http_client_close(c);
            if (status >= 0)
                status = -1;
        }
        break; /* the connection stays open for the next request */
    }
    xSemaphoreGive(lock);
    return status;
}

int http_request(const char *method, const char *url, const char *bearer, const char *body, http_response_t *resp)
{
    memset(resp, 0, sizeof(*resp));
    sink_t s = {NULL, NULL, resp, false};
    int status = do_request(method, url, bearer, body, &s);
    if (s.overflow) {
        ESP_LOGW(TAG, "response over %d bytes dropped", MAX_BODY);
        http_response_free(resp);
        status = -1;
    }
    resp->status = status;
    if (status < 0)
        return -1;
    if (!resp->body) {
        resp->body = calloc(1, 1);
        resp->len = 0;
    }
    return 0;
}

int http_request_stream(const char *method, const char *url, const char *bearer, const char *body,
                        http_data_cb_t on_data, void *ctx)
{
    sink_t s = {on_data, ctx, NULL, false};
    return do_request(method, url, bearer, body, &s);
}

void http_response_free(http_response_t *resp)
{
    free(resp->body);
    resp->body = NULL;
    resp->len = 0;
}
