"""Bring-up check for the real panel — no account needed.

    uv run --extra pi --extra display python -m huckdeck.dev_oledtest

Cycles the demo screens from dev_display on the OLED (3s each) using the pins
in config.yaml, so wiring and the SPI jumpers can be checked before the
service is touched.
"""

from __future__ import annotations

import time

import yaml

from .dev_display import NOW, SCENES
from .feedback.display import render
from .feedback.display_oled import open_device, show
from .main import _find_config


def main() -> None:
    config = yaml.safe_load(_find_config().read_text())
    pins = config["gpio"]["display"]
    brightness = int((config.get("display") or {}).get("brightness", 80))
    device = open_device(int(pins["dc"]), int(pins["rst"]), brightness)
    print(f"Panel {device.width}x{device.height} up; showing {len(SCENES)} screens")
    try:
        for caption, state, overlay in SCENES:
            print(f"  {caption}")
            show(device, render(state, NOW, overlay))
            time.sleep(3)
    finally:
        device.cleanup()


if __name__ == "__main__":
    main()
