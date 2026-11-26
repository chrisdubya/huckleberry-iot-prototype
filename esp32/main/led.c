#include "led.h"

#include <math.h>
#include <string.h>

#include "driver/ledc.h"
#include "driver/rmt_tx.h"
#include "esp_log.h"
#include "sdkconfig.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "platform.h"

typedef struct {
    float r, g, b;
} color_t;

static const color_t GREEN = {0, 1, 0};
static const color_t RED = {1, 0, 0};
static const color_t WHITE = {1, 1, 1};
static const color_t YELLOW = {1, 0.6f, 0}; /* pure (1,1,0) reads greenish on RGB LEDs */
static const color_t BLUE = {0, 0, 1};
static const color_t PURPLE = {1, 0, 1};
static const color_t OFF = {0, 0, 0};
static const color_t DIM_GREEN = {0, 0.15f, 0}; /* startup confirmation; nursing session */
static const color_t DIM_RED = {0.15f, 0, 0};   /* sleep session */

#define SUCCESS_BLINKS 2
#define SUCCESS_ON_S 0.15
#define SUCCESS_OFF_S 0.15
#define PULSE_FADE_S 2.0 /* each direction of the session breathe effect */
#define STARTUP_GREEN_S 3.0
#define TICK_MS 20

typedef enum { MODE_SOLID, MODE_BLINK, MODE_PULSE } mode_t_;

static struct {
    int pins[3];
    mode_t_ mode;
    color_t on, off;
    double on_s, off_s;   /* blink */
    int blinks_left;      /* blink: -1 = forever */
    double started;       /* uptime when the pattern started */
    bool revert_pending;
    double revert_at;
    bool retrying;
    bool sleep_active, nursing_active;
} L;

static const ledc_channel_t CHANNELS[3] = {LEDC_CHANNEL_0, LEDC_CHANNEL_1, LEDC_CHANNEL_2};

/* -- onboard addressable LED (WS2812), optional ------------------------ */

static rmt_channel_handle_t px_chan;
static rmt_encoder_handle_t px_enc;
static uint8_t px_last[3] = {1, 1, 1}; /* anything but "off", so the first write goes out */

static void pixel_init(int pin)
{
    if (pin < 0)
        return;
    rmt_tx_channel_config_t chan = {
        .gpio_num = pin,
        .clk_src = RMT_CLK_SRC_DEFAULT,
        .resolution_hz = 10 * 1000 * 1000, /* 0.1 us ticks */
        .mem_block_symbols = 64,
        .trans_queue_depth = 1,
    };
    rmt_bytes_encoder_config_t enc = {
        .bit0 = {.level0 = 1, .duration0 = 3, .level1 = 0, .duration1 = 9},
        .bit1 = {.level0 = 1, .duration0 = 9, .level1 = 0, .duration1 = 3},
        .flags.msb_first = 1,
    };
    if (rmt_new_tx_channel(&chan, &px_chan) != ESP_OK || rmt_new_bytes_encoder(&enc, &px_enc) != ESP_OK ||
        rmt_enable(px_chan) != ESP_OK) {
        ESP_LOGW("led", "Onboard LED on GPIO %d could not be set up", pin);
        px_chan = NULL;
        return;
    }
    ESP_LOGI("led", "Onboard RGB LED on GPIO %d", pin);
}

static void pixel_write(color_t c)
{
    if (!px_chan)
        return;
    float k = CONFIG_HUCKDECK_LED_PIXEL_BRIGHTNESS / 100.0f * 255;
    uint8_t grb[3] = {
        (uint8_t)(fminf(fmaxf(c.g, 0), 1) * k),
        (uint8_t)(fminf(fmaxf(c.r, 0), 1) * k),
        (uint8_t)(fminf(fmaxf(c.b, 0), 1) * k),
    };
    if (memcmp(grb, px_last, 3) == 0)
        return;
    memcpy(px_last, grb, 3);
    rmt_transmit_config_t tx = {.loop_count = 0};
    if (rmt_transmit(px_chan, px_enc, grb, sizeof(grb), &tx) == ESP_OK)
        rmt_tx_wait_all_done(px_chan, 10);
}

static void write_color(color_t c)
{
    pixel_write(c);
    float v[3] = {c.r, c.g, c.b};
    for (int i = 0; i < 3; i++) {
        if (L.pins[i] < 0)
            continue;
        uint32_t duty = (uint32_t)(fminf(fmaxf(v[i], 0), 1) * 8191);
        ledc_set_duty(LEDC_LOW_SPEED_MODE, CHANNELS[i], duty);
        ledc_update_duty(LEDC_LOW_SPEED_MODE, CHANNELS[i]);
    }
}

static color_t mix(color_t a, color_t b, float t)
{
    color_t c = {a.r + (b.r - a.r) * t, a.g + (b.g - a.g) * t, a.b + (b.b - a.b) * t};
    return c;
}

static void solid(color_t c)
{
    L.mode = MODE_SOLID;
    L.on = c;
    L.started = hd_uptime();
}

static void blink(double on_s, double off_s, color_t on, color_t off, int n)
{
    L.mode = MODE_BLINK;
    L.on = on;
    L.off = off;
    L.on_s = on_s;
    L.off_s = off_s;
    L.blinks_left = n;
    L.started = hd_uptime();
}

static void pulse(color_t on, color_t off)
{
    L.mode = MODE_PULSE;
    L.on = on;
    L.off = off;
    L.started = hd_uptime();
}

static void show_idle(void)
{
    if (L.sleep_active && L.nursing_active)
        pulse(DIM_RED, DIM_GREEN); /* shouldn't happen in practice: fade between colours */
    else if (L.sleep_active)
        pulse(DIM_RED, OFF);
    else if (L.nursing_active)
        pulse(DIM_GREEN, OFF);
    else
        solid(OFF);
}

static void revert_after(double seconds)
{
    L.revert_pending = true;
    L.revert_at = hd_uptime() + seconds;
}

static color_t current_color(double now)
{
    double t = now - L.started;
    switch (L.mode) {
    case MODE_SOLID:
        return L.on;
    case MODE_BLINK: {
        double period = L.on_s + L.off_s;
        int cycle = (int)(t / period);
        if (L.blinks_left >= 0 && cycle >= L.blinks_left)
            return L.off;
        return fmod(t, period) < L.on_s ? L.on : L.off;
    }
    case MODE_PULSE: {
        double phase = fmod(t, 2 * PULSE_FADE_S);
        float k = phase < PULSE_FADE_S ? (float)(phase / PULSE_FADE_S) : (float)(2 - phase / PULSE_FADE_S);
        return mix(L.off, L.on, k);
    }
    }
    return OFF;
}

static void task(void *arg)
{
    (void)arg;
    for (;;) {
        hd_lock();
        double now = hd_uptime();
        if (L.revert_pending && now >= L.revert_at) {
            L.revert_pending = false;
            show_idle();
        }
        color_t c = current_color(now);
        hd_unlock();
        write_color(c);
        vTaskDelay(pdMS_TO_TICKS(TICK_MS));
    }
}

void led_init(int red_pin, int green_pin, int blue_pin)
{
    memset(&L, 0, sizeof(L));
    L.pins[0] = red_pin;
    L.pins[1] = green_pin;
    L.pins[2] = blue_pin;
    ledc_timer_config_t timer = {
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .duty_resolution = LEDC_TIMER_13_BIT,
        .timer_num = LEDC_TIMER_0,
        .freq_hz = 5000,
        .clk_cfg = LEDC_AUTO_CLK,
    };
    ledc_timer_config(&timer);
    for (int i = 0; i < 3; i++) {
        if (L.pins[i] < 0)
            continue;
        ledc_channel_config_t ch = {
            .gpio_num = L.pins[i],
            .speed_mode = LEDC_LOW_SPEED_MODE,
            .channel = CHANNELS[i],
            .timer_sel = LEDC_TIMER_0,
            .duty = 0,
            .hpoint = 0,
        };
        ledc_channel_config(&ch);
    }
    pixel_init(CONFIG_HUCKDECK_LED_PIXEL_GPIO);
    solid(DIM_GREEN); /* "I'm running" — reverts to idle shortly */
    revert_after(STARTUP_GREEN_S);
    xTaskCreate(task, "led", 2048, NULL, 5, NULL);
}

void led_set_sessions(bool sleep_active, bool nursing_active)
{
    hd_lock();
    L.sleep_active = sleep_active;
    L.nursing_active = nursing_active;
    if (!L.revert_pending && !L.retrying)
        show_idle();
    hd_unlock();
}

static color_t success_color(action_t action)
{
    switch (action) {
    case ACT_PEE: return YELLOW;
    case ACT_POO: return PURPLE; /* the poop button is black, which an LED can't show */
    case ACT_BOTH: return WHITE;
    case ACT_BOTTLE: return BLUE;
    case ACT_SLEEP_START:
    case ACT_SLEEP_STOP: return RED;
    default: return GREEN;
    }
}

void led_on_event(status_t status, action_t action, const char *detail)
{
    (void)detail;
    hd_lock();
    switch (status) {
    case ST_SUCCESS:
        L.retrying = false;
        blink(SUCCESS_ON_S, SUCCESS_OFF_S, success_color(action), OFF, SUCCESS_BLINKS);
        revert_after(SUCCESS_BLINKS * (SUCCESS_ON_S + SUCCESS_OFF_S) + 0.05);
        break;
    case ST_RETRYING:
        L.retrying = true;
        L.revert_pending = false;
        blink(0.25, 0.25, RED, OFF, -1);
        break;
    case ST_FAILED:
        L.retrying = false;
        solid(RED);
        revert_after(5.0);
        break;
    default:
        break; /* "remote" session changes reach the LED via led_set_sessions() */
    }
    hd_unlock();
}
