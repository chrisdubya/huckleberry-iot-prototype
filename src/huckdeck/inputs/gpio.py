"""GPIO button input for the Pi: each wired button is a deck button.

Same run() contract as keyboard.py, but `buttons` maps BCM pin numbers
(as strings, from config.yaml) to event names. Buttons are wired to
ground and use internal pull-ups. gpiozero fires callbacks on its own
thread; presses are marshalled onto the asyncio loop.

Runs until SIGTERM/SIGINT (systemd stop or Ctrl-C).
"""

from __future__ import annotations

import asyncio
import logging
import signal

from ..dispatcher import Dispatcher

_LOGGER = logging.getLogger(__name__)

BOUNCE_SECONDS = 0.05  # contact bounce only; sleepy double-taps are debounced in Dispatcher


async def run(dispatcher: Dispatcher, buttons: dict[str, str]) -> None:
    from gpiozero import Button  # deferred: Pi-only dependency

    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    held: list[Button] = []
    try:
        for pin_str, event_name in buttons.items():
            button = Button(int(pin_str), pull_up=True, bounce_time=BOUNCE_SECONDS)

            def on_press(name: str = event_name) -> None:
                loop.call_soon_threadsafe(dispatcher.press, name)

            button.when_pressed = on_press
            held.append(button)
            _LOGGER.info("GPIO %s -> %s", pin_str, event_name)

        await stop.wait()
    finally:
        for button in held:
            button.close()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)
