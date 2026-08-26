"""huckdeck entry point: auth, then run the input loop until quit."""

from __future__ import annotations

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
from .inputs import keyboard

STATE_PATH = Path.home() / ".huckdeck.state.json"


def _find_config() -> Path:
    for candidate in (Path.cwd() / "config.yaml", Path(__file__).resolve().parents[2] / "config.yaml"):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("config.yaml not found (run from the project directory)")

EVENT_KEY_HELP = {
    "pee": "pee",
    "poo": "poop",
    "both": "pee+poop",
    "bottle": "bottle",
    "sleep_toggle": "sleep start/stop",
    "nursing_toggle": "nursing start/stop",
}


def _print_status(line: str) -> None:
    print(f"\r\033[K{line}")


async def main() -> int:
    load_dotenv()
    email = os.environ.get("HUCKLEBERRY_EMAIL")
    password = os.environ.get("HUCKLEBERRY_PASSWORD")
    timezone = os.environ.get("HUCKLEBERRY_TZ", "America/New_York")
    if not email or not password:
        print("Set HUCKLEBERRY_EMAIL and HUCKLEBERRY_PASSWORD in .env (see .env.example)")
        return 1

    config = yaml.safe_load(_find_config().read_text())
    buttons: dict[str, str] = {str(k): v for k, v in config["buttons"].items()}

    async with aiohttp.ClientSession() as websession:
        client = HuckClient(email, password, timezone, websession, config)
        print("Authenticating with Huckleberry…")
        await client.connect()
        print(f"Connected. Logging events for: {client.child_name}\n")

        dispatcher = Dispatcher(
            client=client,
            state_path=STATE_PATH,
            debounce_seconds=float(config.get("debounce_seconds", 2)),
            on_status=_print_status,
        )
        if dispatcher.sleep_active:
            print("● Resumed with a sleep session in progress (press its key to complete it)")
        if dispatcher.nursing_active:
            print("● Resumed with a nursing session in progress (press its key to complete it)")

        for key, event in buttons.items():
            print(f"  [{key}] {EVENT_KEY_HELP.get(event, event)}")
        print("  [q] quit\n")

        consumer = asyncio.create_task(dispatcher.run())
        try:
            await keyboard.run(dispatcher, buttons)
        finally:
            # Let queued sends finish before tearing down the session.
            await dispatcher.wait_idle()
            consumer.cancel()
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
