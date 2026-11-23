/* platform.h on ESP-IDF: FreeRTOS lock, wall clock from SNTP, NVS state. */
#include <stdarg.h>
#include <stdio.h>
#include <sys/time.h>

#include "esp_log.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "platform.h"

static SemaphoreHandle_t lock;
static SemaphoreHandle_t net_lock;
static SemaphoreHandle_t event_sem;
static bool time_valid;

void platform_init(void)
{
    lock = xSemaphoreCreateRecursiveMutex();
    net_lock = xSemaphoreCreateRecursiveMutex();
    event_sem = xSemaphoreCreateBinary();
}

void platform_set_time_valid(void)
{
    time_valid = true;
}

void hd_lock(void)
{
    xSemaphoreTakeRecursive(lock, portMAX_DELAY);
}

void hd_unlock(void)
{
    xSemaphoreGiveRecursive(lock);
}

void hd_net_lock(void)
{
    xSemaphoreTakeRecursive(net_lock, portMAX_DELAY);
}

void hd_net_unlock(void)
{
    xSemaphoreGiveRecursive(net_lock);
}

double hd_now(void)
{
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return tv.tv_sec + tv.tv_usec / 1e6;
}

bool hd_time_valid(void)
{
    return time_valid;
}

double hd_uptime(void)
{
    return esp_timer_get_time() / 1e6;
}

void hd_sleep_ms(int ms)
{
    vTaskDelay(pdMS_TO_TICKS(ms));
}

void hd_event_signal(void)
{
    xSemaphoreGive(event_sem);
}

void hd_event_wait(int timeout_ms)
{
    xSemaphoreTake(event_sem, pdMS_TO_TICKS(timeout_ms));
}

void hd_state_load(bool *sleep_active, bool *nursing_active)
{
    *sleep_active = *nursing_active = false;
    nvs_handle_t h;
    if (nvs_open("huckdeck", NVS_READONLY, &h) != ESP_OK)
        return;
    uint8_t v;
    if (nvs_get_u8(h, "sleep", &v) == ESP_OK)
        *sleep_active = v;
    if (nvs_get_u8(h, "nursing", &v) == ESP_OK)
        *nursing_active = v;
    nvs_close(h);
}

void hd_state_save(bool sleep_active, bool nursing_active)
{
    nvs_handle_t h;
    if (nvs_open("huckdeck", NVS_READWRITE, &h) != ESP_OK)
        return;
    nvs_set_u8(h, "sleep", sleep_active);
    nvs_set_u8(h, "nursing", nursing_active);
    nvs_commit(h);
    nvs_close(h);
}

void hd_random_hex(char *out, int n)
{
    static const char *hex = "0123456789abcdef";
    for (int i = 0; i < n; i++)
        out[i] = hex[esp_random() & 15];
    out[n] = 0;
}

void hd_log(const char *tag, const char *fmt, ...)
{
    char buf[256];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    ESP_LOGI(tag, "%s", buf);
}
