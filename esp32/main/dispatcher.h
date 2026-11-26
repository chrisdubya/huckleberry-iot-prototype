/* Press-to-event dispatcher: debounce, toggle state, queue, retry.
 * Port of src/huckdeck/dispatcher.py; see that file for the reasoning.
 *
 * The input layer calls dispatcher_press() with a button event; the
 * timestamp is captured there. Sends happen in dispatcher_process(), run
 * from its own task, with retries so a WiFi blip never loses or re-times
 * an event. Huckleberry owns the truth about running sessions: a toggle
 * press guesses start/stop for instant feedback, the consumer checks the
 * live timer before sending, and dispatcher_sync_remote() takes polled
 * changes so the LED follows sessions started elsewhere.
 */
#pragma once

#include <stdbool.h>

#include "events.h"

#define DISPATCHER_RETRIES 3
#define DISPATCHER_BACKOFF_BASE_SECONDS 2 /* 2s, 4s, 8s */

void dispatcher_init(double debounce_seconds, status_cb_t on_event);
/* Input side: resolve toggles, enqueue. Safe from any task. */
void dispatcher_press(button_event_t event);
/* Network side: send every queued event (with retries); returns when the queue is empty. */
void dispatcher_process(void);
/* Called when the server's timer state is seen to change (poll task). */
void dispatcher_sync_remote(session_kind_t kind, bool active);
bool dispatcher_sleep_active(void);
bool dispatcher_nursing_active(void);
bool dispatcher_idle(void);
