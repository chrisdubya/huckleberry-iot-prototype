"""Dev-only check of the GPIO input + LED against gpiozero's mock pins.

Run on any machine (no Pi hardware needed):

    uv run --extra pi python -m huckdeck.dev_mocktest

Simulates button presses by driving the mock pins low and asserts the
dispatcher receives the right events; exercises every StatusLed state.
"""

from __future__ import annotations

import asyncio
import os
import signal
from datetime import datetime
from pathlib import Path

os.environ["GPIOZERO_PIN_FACTORY"] = "mock"

from gpiozero import Device  # noqa: E402
from gpiozero.pins.mock import MockFactory, MockPWMPin  # noqa: E402

Device.pin_factory = MockFactory(pin_class=MockPWMPin)

from .dispatcher import Dispatcher  # noqa: E402
from .feedback.led import StatusLed  # noqa: E402
from .inputs import gpio  # noqa: E402

GPIO_BUTTONS = {"5": "pee", "6": "poo", "13": "both", "19": "bottle", "26": "sleep_toggle", "16": "nursing_toggle"}


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def log_diaper(self, mode: str, pressed_at: datetime) -> None:
        self.calls.append(f"diaper:{mode}")

    async def log_bottle(self, pressed_at: datetime) -> None:
        self.calls.append("bottle")

    async def start_sleep(self) -> None:
        self.calls.append("sleep_start")

    async def complete_sleep(self) -> None:
        self.calls.append("sleep_stop")

    async def start_nursing(self) -> None:
        self.calls.append("nursing_start")

    async def complete_nursing(self) -> None:
        self.calls.append("nursing_stop")


def press_pin(bcm: int) -> None:
    pin = Device.pin_factory.pin(bcm)
    pin.drive_low()
    pin.drive_high()


async def main() -> None:
    state = Path("/tmp/huckdeck-mocktest-state.json")
    state.unlink(missing_ok=True)

    led = StatusLed(17, 27, 22)
    client = FakeClient()
    statuses: list[str] = []

    def on_event(status: str, action: str, detail: str) -> None:
        statuses.append(status)
        led.on_event(status, action, detail)
        led.set_sessions(dispatcher.sleep_active, dispatcher.nursing_active)

    dispatcher = Dispatcher(client=client, state_path=state, debounce_seconds=0.0, on_event=on_event)
    consumer = asyncio.create_task(dispatcher.run())
    input_task = asyncio.create_task(gpio.run(dispatcher, GPIO_BUTTONS))
    await asyncio.sleep(0.2)  # let gpio.run() register buttons

    for bcm in (5, 6, 13, 19, 26, 16):
        press_pin(bcm)
        await asyncio.sleep(0.15)  # > bounce_time so gpiozero registers each edge
    await dispatcher.wait_idle()

    expected = ["diaper:pee", "diaper:poo", "diaper:both", "bottle", "sleep_start", "nursing_start"]
    assert client.calls == expected, f"calls mismatch: {client.calls}"
    assert dispatcher.sleep_active and dispatcher.nursing_active
    assert statuses.count("sending") == 6 and statuses.count("success") == 6, statuses

    press_pin(26)  # sleep toggle -> stop
    await asyncio.sleep(0.15)
    await dispatcher.wait_idle()
    assert client.calls[-1] == "sleep_stop" and not dispatcher.sleep_active

    # Retry blink survives the set_sessions() that follows every event...
    on_event("retrying", "pee", "simulated retry")
    await asyncio.sleep(0.05)
    assert led._led._blink_thread is not None, "retry blink clobbered by set_sessions"
    assert led._led.color == (1, 0, 0), led._led.color
    # ...and is cleared by the final outcome
    on_event("failed", "pee", "simulated loss")
    await asyncio.sleep(0.1)
    assert led._led._blink_thread is None and led._led.color == (1, 0, 0)
    assert not led._retrying

    os.kill(os.getpid(), signal.SIGTERM)  # gpio.run exits like a systemd stop
    await input_task
    consumer.cancel()
    led.close()
    state.unlink(missing_ok=True)
    print("mock GPIO test OK — 6 buttons, toggles, LED states all good")


if __name__ == "__main__":
    asyncio.run(main())
