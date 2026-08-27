"""RGB status LED feedback (gpiozero.RGBLED).

Colors:
  green flash   — event logged successfully
  red blink     — retrying a send
  solid red 5s  — event lost after all retries
  dim blue      — a sleep or nursing session is in progress (idle color)
  off           — idle, no session

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
DIM_BLUE = (0, 0, 0.15)
OFF = (0, 0, 0)


class StatusLed:
    def __init__(self, red_pin: int, green_pin: int, blue_pin: int) -> None:
        from gpiozero import RGBLED  # deferred: Pi-only dependency

        self._led = RGBLED(red=red_pin, green=green_pin, blue=blue_pin)
        self._idle_color = OFF
        self._revert_timer: threading.Timer | None = None

    def set_session_active(self, active: bool) -> None:
        """Called whenever sleep/nursing toggle state may have changed."""
        self._idle_color = DIM_BLUE if active else OFF
        if self._revert_timer is None:
            self._led.color = self._idle_color

    def _flash(self, color: tuple, seconds: float) -> None:
        if self._revert_timer is not None:
            self._revert_timer.cancel()
        self._led.color = color
        self._revert_timer = threading.Timer(seconds, self._revert)
        self._revert_timer.daemon = True
        self._revert_timer.start()

    def _revert(self) -> None:
        self._revert_timer = None
        self._led.color = self._idle_color

    def on_event(self, status: str, action: str, detail: str) -> None:
        match status:
            case "success":
                self._flash(GREEN, 1.0)
            case "retrying":
                if self._revert_timer is not None:
                    self._revert_timer.cancel()
                    self._revert_timer = None
                self._led.blink(on_time=0.25, off_time=0.25, on_color=RED, off_color=OFF)
            case "failed":
                self._flash(RED, 5.0)
            case "sending" | "ignored":
                pass

    def close(self) -> None:
        if self._revert_timer is not None:
            self._revert_timer.cancel()
        self._led.off()
        self._led.close()


class NullStatusLed:
    """Stand-in for keyboard mode / machines without an LED."""

    def set_session_active(self, active: bool) -> None:
        pass

    def on_event(self, status: str, action: str, detail: str) -> None:
        pass

    def close(self) -> None:
        pass
