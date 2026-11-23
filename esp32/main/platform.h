/* The few OS services the portable modules need, implemented once for the
 * ESP32 (platform_esp.c) and once for the host preview/tests (host/platform_host.c). */
#pragma once

#include <stdbool.h>

/* Recursive lock around the display/dispatcher state. */
void hd_lock(void);
void hd_unlock(void);
/* Recursive lock serialising everything that touches the auth token and the network. */
void hd_net_lock(void);
void hd_net_unlock(void);
/* Wall-clock seconds since the epoch, with fraction. */
double hd_now(void);
/* True once the clock has been set from NTP (always true on the host). */
bool hd_time_valid(void);
/* Monotonic seconds since boot. */
double hd_uptime(void);
void hd_sleep_ms(int ms);
/* Wake the dispatcher task / block it until a press or timeout. */
void hd_event_signal(void);
void hd_event_wait(int timeout_ms);
/* Persisted toggle state (~/.huckdeck.state.json on the Pi; NVS here). */
void hd_state_load(bool *sleep_active, bool *nursing_active);
void hd_state_save(bool sleep_active, bool nursing_active);
/* n random lowercase hex digits plus a NUL. */
void hd_random_hex(char *out, int n);
/* Short log line (printf-style). */
void hd_log(const char *tag, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
