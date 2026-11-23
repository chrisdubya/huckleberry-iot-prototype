#include "buttons.h"

#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "buttons";

#define SAMPLE_MS 5
#define BOUNCE_US 50000 /* contact bounce only; sleepy double-taps are debounced in the dispatcher */

static int pins_[BTN_COUNT];
static button_cb_t on_press_;

static void task(void *arg)
{
    (void)arg;
    int stable[BTN_COUNT];
    int64_t last_change[BTN_COUNT];
    for (int i = 0; i < BTN_COUNT; i++) {
        stable[i] = 1;
        last_change[i] = 0;
    }
    for (;;) {
        int64_t now = esp_timer_get_time();
        for (int i = 0; i < BTN_COUNT; i++) {
            if (pins_[i] < 0)
                continue;
            int raw = gpio_get_level(pins_[i]);
            if (raw != stable[i] && now - last_change[i] >= BOUNCE_US) {
                stable[i] = raw;
                last_change[i] = now;
                if (raw == 0) /* pressed: pulled to ground */
                    on_press_((button_event_t)i);
            }
        }
        vTaskDelay(pdMS_TO_TICKS(SAMPLE_MS));
    }
}

void buttons_start(const int *pins, button_cb_t on_press)
{
    on_press_ = on_press;
    uint64_t mask = 0;
    for (int i = 0; i < BTN_COUNT; i++) {
        pins_[i] = pins[i];
        if (pins[i] >= 0) {
            mask |= 1ULL << pins[i];
            ESP_LOGI(TAG, "GPIO %d -> %s", pins[i], BUTTON_EVENT_NAMES[i]);
        }
    }
    gpio_config_t cfg = {
        .pin_bit_mask = mask,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    if (mask)
        gpio_config(&cfg);
    xTaskCreate(task, "buttons", 3072, NULL, 10, NULL);
}
