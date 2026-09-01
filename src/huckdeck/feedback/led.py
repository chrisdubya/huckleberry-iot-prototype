"""RGB status LED feedback (gpiozero.RGBLED).

Colors:
  dim green      — device on and ready, no session in progress
  pulsing blue   — a sleep session is in progress (fades in and out)
  pulsing purple — a nursing session is in progress (fades in and out)
  blue↔purple    — both sessions somehow active at once: fade between colors
  green blinks   — event logged successfully (double blink, then back to idle)
  red blink      — retrying a send
  solid red 5s   — event lost after all retries

gpiozero is only imported here, so keyboard-mode runs on the Mac never
need it installed. Uses gpiozero's built-in blink() which runs on its own
background thread — no asyncio coupling.
"""

from __future__ import annotations

import logging
import threading

_LOGGER = logging.getLogger(__name__)

GREEN = (0, 1, 0)
RED = (1, 0, 0)
OFF = (0, 0, 0)
DIM_GREEN = (0, 0.15, 0)  # ready — dim so the bright success blink stands out
DIM_BLUE = (0, 0, 0.15)  # sleep session
DIM_PURPLE = (0.15, 0, 0.15)  # nursing session

SUCCESS_BLINKS = 2
SUCCESS_ON_SECONDS = 0.15
SUCCESS_OFF_SECONDS = 0.15

PULSE_FADE_SECONDS = 1.5  # each direction of the session breathe effect


class StatusLed:
    def __init__(self, red_pin: int, green_pin: int, blue_pin: int) -> None:
        from gpiozero import RGBLED  # deferred: Pi-only dependency

        self._led = RGBLED(red=red_pin, green=green_pin, blue=blue_pin)
        self._sleep_active = False
        self._nursing_active = False
        self._revert_timer: threading.Timer | None = None
        self._show_idle()

    def set_sessions(self, sleep_active: bool, nursing_active: bool) -> None:
        """Called whenever sleep/nursing toggle state may have changed."""
        self._sleep_active = sleep_active
        self._nursing_active = nursing_active
        if self._revert_timer is None:
            self._show_idle()

    def _show_idle(self) -> None:
        if self._sleep_active and self._nursing_active:
            # Shouldn't happen in practice, but if it does: fade between colors.
            self._pulse(DIM_BLUE, DIM_PURPLE)
        elif self._sleep_active:
            self._pulse(DIM_BLUE, OFF)
        elif self._nursing_active:
            self._pulse(DIM_PURPLE, OFF)
        else:
            self._led.color = DIM_GREEN

    def _pulse(self, on_color: tuple, off_color: tuple) -> None:
        self._led.pulse(
            fade_in_time=PULSE_FADE_SECONDS,
            fade_out_time=PULSE_FADE_SECONDS,
            on_color=on_color,
            off_color=off_color,
        )

    def _cancel_revert(self) -> None:
        if self._revert_timer is not None:
            self._revert_timer.cancel()
            self._revert_timer = None

    def _revert_after(self, seconds: float) -> None:
        self._cancel_revert()
        self._revert_timer = threading.Timer(seconds, self._revert)
        self._revert_timer.daemon = True
        self._revert_timer.start()

    def _revert(self) -> None:
        self._revert_timer = None
        self._show_idle()

    def on_event(self, status: str, action: str, detail: str) -> None:
        match status:
            case "success":
                self._led.blink(
                    on_time=SUCCESS_ON_SECONDS,
                    off_time=SUCCESS_OFF_SECONDS,
                    on_color=GREEN,
                    off_color=OFF,
                    n=SUCCESS_BLINKS,
                )
                self._revert_after(
                    SUCCESS_BLINKS * (SUCCESS_ON_SECONDS + SUCCESS_OFF_SECONDS) + 0.05
                )
            case "retrying":
                self._cancel_revert()
                self._led.blink(on_time=0.25, off_time=0.25, on_color=RED, off_color=OFF)
            case "failed":
                self._led.color = RED
                self._revert_after(5.0)
            case "sending" | "ignored":
                pass

    def close(self) -> None:
        self._cancel_revert()
        self._led.off()
        self._led.close()


class NullStatusLed:
    """Stand-in for keyboard mode / machines without an LED."""

    def set_sessions(self, sleep_active: bool, nursing_active: bool) -> None:
        pass

    def on_event(self, status: str, action: str, detail: str) -> None:
        pass

    def close(self) -> None:
        pass
