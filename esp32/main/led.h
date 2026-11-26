/* RGB status LED, port of feedback/led.py (same colours and timings). */
#pragma once

#include <stdbool.h>

#include "events.h"

void led_init(int red_pin, int green_pin, int blue_pin);
void led_set_sessions(bool sleep_active, bool nursing_active);
void led_on_event(status_t status, action_t action, const char *detail);
