"""huckdeck entry point: auth, then run the input loop until quit."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path

import aiohttp
import yaml
from dotenv import load_dotenv

from .client import HuckClient
from .dispatcher import Dispatcher
from .feedback.display import NullDisplay
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

STATUS_PREFIX = {"sending": "⏳", "success": "✓", "retrying": "⚠", "failed": "✗", "remote": "↻"}


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
    parser.add_argument("--display", choices=["none", "sim", "oled"], help="override config.yaml display driver")
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

    display_config = config.get("display") or {}
    display_driver = args.display or display_config.get("driver", "none")
    if display_driver == "oled" and args.display is None and input_mode != "gpio":
        display_driver = "none"  # the panel is Pi hardware, like the buttons; keyboard runs skip it
    display = NullDisplay()
    sim_runner = None
    display_options = {
        "mode": display_config.get("mode", "ticker"),
        "brightness": int(display_config.get("brightness", 80)),
        "dim_brightness": int(display_config.get("dim_brightness", 20)),
        "dim_after_seconds": float(display_config.get("dim_after_seconds", 60)),
        "ticker_speed": float(display_config.get("ticker_speed", 24)),
    }
    day_start_hour = int(display_config.get("day_starts_at", 8))
    if display_driver == "sim":
        from .feedback import display_sim
        from .feedback.display import Display

        display = Display(**display_options)
        sim_port = int(display_config.get("sim_port", 8765))
        sim_runner = await display_sim.start(display, sim_port)
        print(f"Display simulator: http://localhost:{sim_port}/")
    elif display_driver == "oled":
        from .feedback.display_oled import OledDisplay

        pins = config["gpio"]["display"]
        display = OledDisplay(int(pins["dc"]), int(pins["rst"]), **display_options)
        display.start()

    async with aiohttp.ClientSession() as websession:
        client = HuckClient(email, password, timezone, websession, config)
        print("Authenticating with Huckleberry…")
        await client.connect()
        print(f"Connected. Logging events for: {client.child_name}\n")

        dispatcher: Dispatcher | None = None
        totals_due = asyncio.Event()  # set whenever today's counts may have changed

        def on_event(status: str, action: str, detail: str) -> None:
            _print_status(status, action, detail)
            led.on_event(status, action, detail)
            display.on_event(status, action, detail)
            if status in ("success", "remote"):
                totals_due.set()
            if dispatcher is not None:
                led.set_sessions(dispatcher.sleep_active, dispatcher.nursing_active)

        def on_doc(collection: str, doc: object) -> None:
            display.on_doc(collection, doc)
            if collection in ("diaper", "feed"):
                totals_due.set()  # a change made in the app

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

        # Follow sessions started/stopped from the app. Not fatal if it can't
        # connect yet: keep_alive() retries, and presses pull the state anyway.
        try:
            await client.watch_sessions(dispatcher.sync_remote, on_doc)
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).warning("Couldn't start session listeners yet", exc_info=True)

        consumer = asyncio.create_task(dispatcher.run())
        keep_alive = asyncio.create_task(client.keep_alive())
        totals = asyncio.create_task(_refresh_totals(client, display, day_start_hour, totals_due)) if not isinstance(display, NullDisplay) else None
        try:
            await input_module.run(dispatcher, buttons)
        finally:
            # Let queued sends finish before tearing down the session.
            await dispatcher.wait_idle()
            consumer.cancel()
            keep_alive.cancel()
            if totals is not None:
                totals.cancel()
            await client.close()
            led.close()
            display.close()
            if sim_runner is not None:
                await sim_runner.cleanup()
    print("Bye.")
    return 0


TOTALS_REFRESH_SECONDS = 600  # also re-pull on a timer, and at the 8am rollover


async def _refresh_totals(client: HuckClient, display, day_start_hour: int, due: asyncio.Event) -> None:
    """Keep the display's daily counts current: on demand, on a timer, and when the day rolls over."""
    from .client import day_start as _day_start

    last_day = None
    due.set()
    while True:
        try:
            await asyncio.wait_for(due.wait(), timeout=TOTALS_REFRESH_SECONDS)
        except TimeoutError:
            pass
        due.clear()
        try:
            await asyncio.sleep(2)  # let Huckleberry finish writing the interval we just logged
            totals = await client.day_totals(day_start_hour)
            display.set_totals(totals.diapers, totals.nursing_count, totals.nursing_seconds)
            last_day = totals.day_start
        except Exception:  # noqa: BLE001 — offline etc.; the old counts stay up
            logging.getLogger(__name__).warning("Couldn't refresh daily totals", exc_info=True)
        # wake at the next rollover so the counts reset on time
        if last_day is not None:
            loop = asyncio.get_running_loop()
            seconds_to_rollover = (last_day.timestamp() + 86400) - time.time()
            if 0 < seconds_to_rollover < TOTALS_REFRESH_SECONDS:
                loop.call_later(seconds_to_rollover + 1, due.set)


def run() -> None:
    logging.basicConfig(level=logging.WARNING)
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    run()
