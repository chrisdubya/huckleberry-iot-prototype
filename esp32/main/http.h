/* Minimal HTTPS JSON transport. Implemented by http_esp.c on the device and
 * mocked by the host tests. Bodies are JSON (Content-Type application/json). */
#pragma once

#include <stddef.h>

typedef struct {
    int status;   /* HTTP status, or <0 on transport failure */
    char *body;   /* NUL-terminated, heap; free with http_response_free() */
    size_t len;
} http_response_t;

typedef void (*http_data_cb_t)(void *ctx, const char *data, size_t len);

/* Buffered request: the whole response body lands in resp->body (capped; see http_esp.c). */
int http_request(const char *method, const char *url, const char *bearer, const char *body, http_response_t *resp);
/* Streaming request: the body is handed to on_data in chunks; returns the HTTP status (<0 on failure). */
int http_request_stream(const char *method, const char *url, const char *bearer, const char *body,
                        http_data_cb_t on_data, void *ctx);
void http_response_free(http_response_t *resp);
