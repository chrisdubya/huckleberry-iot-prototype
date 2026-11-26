/* Host implementation of platform.h for the preview and tests: single-threaded,
 * no locking, no real sleeping, state kept in memory. */
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/time.h>
#include <time.h>

#include "platform.h"

/* The tests pin the clock; 0 means "use the real one". */
double host_fake_now = 0;
double host_fake_uptime = 0;
static bool saved_sleep, saved_nursing;
int host_sleep_calls;

void hd_lock(void) {}
void hd_unlock(void) {}
void hd_net_lock(void) {}
void hd_net_unlock(void) {}

double hd_now(void)
{
    if (host_fake_now)
        return host_fake_now;
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return tv.tv_sec + tv.tv_usec / 1e6;
}

bool hd_time_valid(void)
{
    return true;
}

double hd_uptime(void)
{
    if (host_fake_now)
        return host_fake_uptime;
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

void hd_sleep_ms(int ms)
{
    (void)ms;
    host_sleep_calls++;
    host_fake_uptime += ms / 1000.0;
}

void hd_event_signal(void) {}
void hd_event_wait(int timeout_ms) { (void)timeout_ms; }

void hd_state_load(bool *sleep_active, bool *nursing_active)
{
    *sleep_active = saved_sleep;
    *nursing_active = saved_nursing;
}

void hd_state_save(bool sleep_active, bool nursing_active)
{
    saved_sleep = sleep_active;
    saved_nursing = nursing_active;
}

void hd_random_hex(char *out, int n)
{
    static const char *hex = "0123456789abcdef";
    for (int i = 0; i < n; i++)
        out[i] = hex[rand() & 15];
    out[n] = 0;
}

void hd_log(const char *tag, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    fprintf(stderr, "[%s] ", tag);
    vfprintf(stderr, fmt, ap);
    fputc('\n', stderr);
    va_end(ap);
}
