/* Six arcade buttons to ground with internal pull-ups, like inputs/gpio.py. */
#pragma once

#include "events.h"

typedef void (*button_cb_t)(button_event_t event);
/* pins[BTN_COUNT] in BUTTON_EVENT_NAMES order; -1 disables a button. */
void buttons_start(const int *pins, button_cb_t on_press);
