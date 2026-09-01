"""huckdeck entry point: auth, then run the input loop until quit."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

import aiohttp
import yaml
from dotenv import load_dotenv

from .client import HuckClient
from .dispatcher import Dispatcher
from .feedback.led import NullStatusLed

STATE_PATH = Path.home() / ".huckdeck.state.json"

EVENT_KEY_HELP = {
    "pee": "pee",
    "poo": "poop",
    "both": "pee+poop",
    "bottle": "bottle",
    "sleep_toggle": "sleep start/stop",
    "nursing_toggle": "nursing start/stop",
}

STATUS_PREFIX = {"sending": "⏳", "success": "✓", "retrying": "⚠", "failed": "✗"}


def _find_config() -> Path:
    for candidate in (Path.cwd() / "config.yaml", Path(__file__).resolve().parents[2] / "config.yaml"):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("config.yaml not found (run from the project directory)")


def _print_status(status: str, action: str, detail: str) -> None:
    if status == "ignored":
        print(f"\r\033[K({detail})")
    else:
        print(f"\r\033[K{STATUS_PREFIX[status]} {detail}")


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="huckdeck")
    parser.add_argument("--input", choices=["keyboard", "gpio"], help="override config.yaml input source")
    args = parser.parse_args(argv)

    load_dotenv()
    email = os.environ.get("HUCKLEBERRY_EMAIL")
    password = os.environ.get("HUCKLEBERRY_PASSWORD")
    timezone = os.environ.get("HUCKLEBERRY_TZ", "America/New_York")
    if not email or not password:
        print("Set HUCKLEBERRY_EMAIL and HUCKLEBERRY_PASSWORD in .env (see .env.example)")
        return 1

    config = yaml.safe_load(_find_config().read_text())
    input_mode = args.input or config.get("input", "keyboard")

    if input_mode == "gpio":
        from .feedback.led import StatusLed
        from .inputs import gpio as input_module

        led_pins = config["gpio"]["led"]
        led = StatusLed(led_pins["red"], led_pins["green"], led_pins["blue"])
        buttons = {str(k): v for k, v in config["gpio"]["buttons"].items()}
    else:
        from .inputs import keyboard as input_module

        led = NullStatusLed()
        buttons = {str(k): v for k, v in config["buttons"].items()}

    async with aiohttp.ClientSession() as websession:
        client = HuckClient(email, password, timezone, websession, config)
        print("Authenticating with Huckleberry…")
        await client.connect()
        print(f"Connected. Logging events for: {client.child_name}\n")

        dispatcher: Dispatcher | None = None

        def on_event(status: str, action: str, detail: str) -> None:
            _print_status(status, action, detail)
            led.on_event(status, action, detail)
            if dispatcher is not None:
                led.set_sessions(dispatcher.sleep_active, dispatcher.nursing_active)

        dispatcher = Dispatcher(
            client=client,
            state_path=STATE_PATH,
            debounce_seconds=float(config.get("debounce_seconds", 2)),
            on_event=on_event,
        )
        led.set_sessions(dispatcher.sleep_active, dispatcher.nursing_active)
        if dispatcher.sleep_active:
            print("● Resumed with a sleep session in progress (press its button to complete it)")
        if dispatcher.nursing_active:
            print("● Resumed with a nursing session in progress (press its button to complete it)")

        if input_mode == "keyboard":
            for key, event in buttons.items():
                print(f"  [{key}] {EVENT_KEY_HELP.get(event, event)}")
            print("  [q] quit\n")

        consumer = asyncio.create_task(dispatcher.run())
        try:
            await input_module.run(dispatcher, buttons)
        finally:
            # Let queued sends finish before tearing down the session.
            await dispatcher.wait_idle()
            consumer.cancel()
            led.close()
    print("Bye.")
    return 0


def run() -> None:
    logging.basicConfig(level=logging.WARNING)
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    run()
