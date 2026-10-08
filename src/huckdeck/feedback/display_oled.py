"""SSD1322 256x64 OLED output over SPI (luma.oled), for the Pi.

Pushes Display.frame() to the panel once a second — only when it changed, so
an idle deck sends nothing. DC/RST are driven through gpiozero like the
buttons and LED, so the whole deck stays on one GPIO stack (no RPi.GPIO).

luma/gpiozero/spidev are only imported here, so Mac runs never need them.
Enable SPI on the Pi first: sudo raspi-config nonint do_spi 0
"""

from __future__ import annotations

import asyncio
import logging

from .display import Display

_LOGGER = logging.getLogger(__name__)

REFRESH_SECONDS = 1.0


class _GpiozeroPins:
    """The small RPi.GPIO-shaped surface luma needs for its DC/RST lines."""

    BCM = "BCM"
    OUT = "out"
    HIGH = 1
    LOW = 0

    def __init__(self) -> None:
        from gpiozero import DigitalOutputDevice

        self._cls = DigitalOutputDevice
        self._pins: dict[int, DigitalOutputDevice] = {}

    def setmode(self, mode) -> None:
        pass

    def setwarnings(self, flag) -> None:
        pass

    def setup(self, pin: int, mode) -> None:
        self._pins[pin] = self._cls(pin)

    def output(self, pin: int, value) -> None:
        self._pins[pin].value = bool(value)

    def cleanup(self) -> None:
        for device in self._pins.values():
            device.close()
        self._pins.clear()


def open_device(dc_pin: int, rst_pin: int, brightness: int):
    """The luma ssd1322 device on SPI0 CE0, plus the pins to close after it.

    luma only releases DC/RST itself when it created the GPIO object, so the
    caller closes `pins` after device.cleanup().
    """
    from luma.core.interface.serial import spi
    from luma.oled.device import ssd1322

    pins = _GpiozeroPins()
    serial = spi(port=0, device=0, gpio=pins, gpio_DC=dc_pin, gpio_RST=rst_pin)
    device = ssd1322(serial, mode="RGB")
    device.contrast(max(0, min(255, brightness)))
    return device, pins


def show(device, frame) -> None:
    device.display(frame.convert(device.mode))


class OledDisplay(Display):
    """Display whose frames go to the panel from a background refresh task."""

    def __init__(self, dc_pin: int, rst_pin: int, brightness: int) -> None:
        super().__init__()
        self._device, self._pins = open_device(dc_pin, rst_pin, brightness)
        self._last: bytes | None = None
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._refresh())

    async def _refresh(self) -> None:
        while True:
            try:
                self.push()
            except Exception:  # noqa: BLE001 — a flaky SPI write shouldn't kill the deck
                _LOGGER.warning("Display refresh failed", exc_info=True)
            await asyncio.sleep(REFRESH_SECONDS)

    def push(self) -> None:
        frame = self.frame()
        raw = frame.tobytes()
        if raw != self._last:
            show(self._device, frame)
            self._last = raw

    def on_event(self, status: str, action: str, detail: str) -> None:
        super().on_event(status, action, detail)
        self.push()  # don't make a button press wait for the next tick

    def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
        self._device.cleanup()
        self._pins.cleanup()
