/* huckdeck on ESP32: boot, join WiFi, sign in, then run the deck forever.
 * Counterpart of src/huckdeck/main.py (gpio input, oled display). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "buttons.h"
#include "dispatcher.h"
#include "display.h"
#include "esp_log.h"
#include "firebase_auth.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "huck_api.h"
#include "led.h"
#include "nvs_flash.h"
#include "platform.h"
#include "platform_esp.h"
#include "sdkconfig.h"
#include "ssd1322.h"
#include "wifi.h"

static const char *TAG = "huckdeck";

#ifdef CONFIG_HUCKDECK_DISPLAY_PANES
#define DISPLAY_PANES 1
#else
#define DISPLAY_PANES 0
#endif

#define TOTALS_REFRESH_SECONDS 600 /* also re-pull on a timer, and at the day rollover */
#define TOTALS_DUE_BIT BIT0

static EventGroupHandle_t totals_events;
static bool display_enabled;

static const char *STATUS_PREFIX[] = {"⏳", "✓", "⚠", "✗", "(ignored)", "↻"};

/* -- status fan-out (main.on_event) ------------------------------------- */

static void on_event(status_t status, action_t action, const char *detail)
{
    ESP_LOGI(TAG, "%s %s", STATUS_PREFIX[status], detail);
    led_on_event(status, action, detail);
    display_on_event(status, action, detail, hd_now());
    if (status == ST_SUCCESS || status == ST_REMOTE)
        xEventGroupSetBits(totals_events, TOTALS_DUE_BIT);
    led_set_sessions(dispatcher_sleep_active(), dispatcher_nursing_active());
}

/* -- polled documents (the Pi's Firestore listeners) -------------------- */

static void on_doc(const char *collection, const cJSON *fields)
{
    if (!strcmp(collection, "sleep")) {
        sleep_doc_t doc;
        huck_parse_sleep(fields, &doc);
        display_on_sleep_doc(&doc);
        dispatcher_sync_remote(KIND_SLEEP, doc.timer_active);
    } else if (!strcmp(collection, "feed")) {
        feed_doc_t doc;
        huck_parse_feed(fields, &doc);
        display_on_feed_doc(&doc);
        dispatcher_sync_remote(KIND_NURSING, doc.timer_active);
        xEventGroupSetBits(totals_events, TOTALS_DUE_BIT); /* a change made in the app */
    } else if (!strcmp(collection, "diaper")) {
        diaper_doc_t doc;
        huck_parse_diaper(fields, &doc);
        display_on_diaper_doc(&doc);
        xEventGroupSetBits(totals_events, TOTALS_DUE_BIT);
    }
    led_set_sessions(dispatcher_sleep_active(), dispatcher_nursing_active());
}

static void poll_task(void *arg)
{
    (void)arg;
    for (;;) {
        /* keep_alive(): the token refresh happens here too, so an overnight run
         * without presses never ends up with an expired token */
        if (firebase_auth_ensure() == 0 && huck_poll(on_doc) != 0)
            ESP_LOGW(TAG, "Poll failed; will retry");
        vTaskDelay(pdMS_TO_TICKS(CONFIG_HUCKDECK_POLL_SECONDS * 1000));
    }
}

static void dispatcher_task(void *arg)
{
    (void)arg;
    for (;;) {
        dispatcher_process();
        hd_event_wait(1000);
    }
}

/* Keep the display's daily counts current: on demand, on a timer, and when the day rolls over. */
static void totals_task(void *arg)
{
    (void)arg;
    double last_day = 0;
    xEventGroupSetBits(totals_events, TOTALS_DUE_BIT);
    for (;;) {
        int timeout_s = TOTALS_REFRESH_SECONDS;
        if (last_day) {
            double to_rollover = last_day + 86400 - hd_now();
            if (to_rollover > 0 && to_rollover < timeout_s)
                timeout_s = (int)to_rollover + 1; /* wake at the next rollover so the counts reset on time */
        }
        xEventGroupWaitBits(totals_events, TOTALS_DUE_BIT, pdTRUE, pdTRUE, pdMS_TO_TICKS(timeout_s * 1000));
        vTaskDelay(pdMS_TO_TICKS(2000)); /* let Huckleberry finish writing the interval we just logged */
        day_totals_t totals;
        if (huck_day_totals(CONFIG_HUCKDECK_DAY_STARTS_AT, &totals) == 0) {
            display_set_totals(totals.diapers, totals.nursing_count, totals.nursing_seconds);
            last_day = totals.day_start;
        } else {
            ESP_LOGW(TAG, "Couldn't refresh daily totals"); /* the old counts stay up */
        }
    }
}

static void display_task(void *arg)
{
    (void)arg;
    static uint8_t px[GFX_WIDTH * GFX_HEIGHT];
    static uint8_t last[GFX_WIDTH * GFX_HEIGHT];
    gfx_t g;
    gfx_init(&g, px, GFX_WIDTH, GFX_HEIGHT);
    int contrast = -1;
    bool first = true;
    for (;;) {
        double now = hd_now();
        int wanted = display_current_brightness(now);
        if (wanted != contrast) {
            ssd1322_contrast(wanted);
            contrast = wanted;
        }
        display_frame(&g, now);
        if (first || memcmp(px, last, sizeof(px))) {
            ssd1322_display(px, GFX_WIDTH, GFX_HEIGHT);
            memcpy(last, px, sizeof(px));
            first = false;
        }
        vTaskDelay(pdMS_TO_TICKS(DISPLAY_PANES ? 1000 : 50));
    }
}

static void on_press(button_event_t event)
{
    dispatcher_press(event);
}

void app_main(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ESP_ERROR_CHECK(nvs_flash_init());
    }
    platform_init();
    totals_events = xEventGroupCreate();
    setenv("TZ", CONFIG_HUCKDECK_TZ, 1);
    tzset();

    led_init(CONFIG_HUCKDECK_LED_RED_GPIO, CONFIG_HUCKDECK_LED_GREEN_GPIO, CONFIG_HUCKDECK_LED_BLUE_GPIO);

    display_config_t dcfg = {
        .mode = DISPLAY_PANES ? DISPLAY_MODE_PANES : DISPLAY_MODE_TICKER,
        .brightness = CONFIG_HUCKDECK_DISPLAY_BRIGHTNESS,
        .dim_brightness = CONFIG_HUCKDECK_DISPLAY_DIM_BRIGHTNESS,
        .dim_after = CONFIG_HUCKDECK_DISPLAY_DIM_AFTER_SECONDS,
        .ticker_speed = CONFIG_HUCKDECK_DISPLAY_TICKER_SPEED,
    };
    display_init(&dcfg, hd_now());
#if CONFIG_HUCKDECK_DISPLAY_ENABLE
    ssd1322_pins_t pins = {CONFIG_HUCKDECK_OLED_MOSI_GPIO, CONFIG_HUCKDECK_OLED_SCLK_GPIO, CONFIG_HUCKDECK_OLED_CS_GPIO,
                           CONFIG_HUCKDECK_OLED_DC_GPIO, CONFIG_HUCKDECK_OLED_RST_GPIO};
    display_enabled = ssd1322_init(&pins, dcfg.brightness) == 0;
    if (display_enabled)
        xTaskCreate(display_task, "display", 6144, NULL, 4, NULL);
#endif

    wifi_connect(CONFIG_HUCKDECK_WIFI_SSID, CONFIG_HUCKDECK_WIFI_PASSWORD);
    wifi_sync_time();
    platform_set_time_valid();
    display_touch(hd_now()); /* the clock just jumped: start the dim timer from now */

    firebase_auth_init(CONFIG_HUCKDECK_HUCKLEBERRY_EMAIL, CONFIG_HUCKDECK_HUCKLEBERRY_PASSWORD);
    huck_config_t hcfg = {
        .amount = CONFIG_HUCKDECK_BOTTLE_AMOUNT,
        .units = CONFIG_HUCKDECK_BOTTLE_UNITS,
        .type = CONFIG_HUCKDECK_BOTTLE_TYPE,
        .nursing_side = CONFIG_HUCKDECK_NURSING_START_SIDE,
        .child_uid = CONFIG_HUCKDECK_CHILD_UID,
    };
    huck_init(&hcfg);
    ESP_LOGI(TAG, "Authenticating with Huckleberry…");
    while (huck_connect() != 0) {
        ESP_LOGE(TAG, "Couldn't sign in; retrying in 10s");
        led_on_event(ST_RETRYING, ACT_NONE, "");
        vTaskDelay(pdMS_TO_TICKS(10000));
    }
    led_on_event(ST_SUCCESS, ACT_NURSING_START, ""); /* a green double blink: connected */
    ESP_LOGI(TAG, "Connected. Logging events for: %s", huck_child_name());

    dispatcher_init(CONFIG_HUCKDECK_DEBOUNCE_SECONDS, on_event);
    led_set_sessions(dispatcher_sleep_active(), dispatcher_nursing_active());
    if (dispatcher_sleep_active())
        ESP_LOGI(TAG, "Resumed with a sleep session in progress (press its button to complete it)");
    if (dispatcher_nursing_active())
        ESP_LOGI(TAG, "Resumed with a nursing session in progress (press its button to complete it)");

    xTaskCreate(dispatcher_task, "dispatcher", 12288, NULL, 6, NULL);
    xTaskCreate(poll_task, "poll", 12288, NULL, 5, NULL);
    if (display_enabled)
        xTaskCreate(totals_task, "totals", 12288, NULL, 3, NULL);

    int button_pins[BTN_COUNT] = {
        CONFIG_HUCKDECK_BTN_PEE_GPIO, CONFIG_HUCKDECK_BTN_POO_GPIO, CONFIG_HUCKDECK_BTN_BOTH_GPIO,
        CONFIG_HUCKDECK_BTN_BOTTLE_GPIO, CONFIG_HUCKDECK_BTN_SLEEP_GPIO, CONFIG_HUCKDECK_BTN_NURSING_GPIO,
    };
    buttons_start(button_pins, on_press);
}
