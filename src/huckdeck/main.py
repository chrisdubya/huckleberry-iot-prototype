"""huckdeck entry point: auth, then run the input loop until quit."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

import aiohttp
import yaml

from . import credentials
from .client import AuthError, HuckClient
from .dispatcher import Dispatcher
from .feedback.led import NullStatusLed
from .setup.flow import SetupFlow
from .setup.wifi import make_wifi

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


async def _wait_for_stop() -> None:
    import signal

    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        await stop.wait()
    finally:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)


def _print_status(status: str, action: str, detail: str) -> None:
    if status == "ignored":
        print(f"\r\033[K({detail})")
    else:
        print(f"\r\033[K{STATUS_PREFIX[status]} {detail}")


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="huckdeck")
    parser.add_argument("--input", choices=["keyboard", "gpio"], help="override config.yaml input source")
    parser.add_argument("--setup", action="store_true", help="force setup mode (hotspot + Wi-Fi page)")
    parser.add_argument("--wifi", choices=["nmcli", "fake"], help="override config.yaml setup.wifi backend")
    parser.add_argument("--setup-port", type=int, help="override config.yaml setup.port")
    parser.add_argument("--identity", action="store_true", help="print the hotspot name/password for the sticker")
    args = parser.parse_args(argv)

    if args.identity:
        from .setup import identity as identity_mod

        ident = identity_mod.load_or_create()
        print(f"Hotspot:  {ident.ssid}\nPassword: {ident.password}\nQR text:  {ident.wifi_qr_payload}")
        return 0

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

    # Setup mode. On the Pi the web page always runs (huckdeck.local); with
    # no saved Wi-Fi it first runs the hotspot flow. --setup forces that.
    setup_cfg = config.get("setup", {})
    wifi = make_wifi(args.wifi or setup_cfg.get("wifi", "nmcli"))
    setup_port = args.setup_port or int(setup_cfg.get("port", 80))
    flow: SetupFlow | None = None
    if args.setup or input_mode == "gpio":
        from .setup import identity as identity_mod

        flow = SetupFlow(wifi, led, identity_mod.load_or_create(), port=setup_port)
        if args.setup or not await wifi.has_saved_network():
            await flow.run_wifi()
        else:
            await flow.serve()

    creds = credentials.load()
    if creds is None and flow is None:
        print("Set HUCKLEBERRY_EMAIL and HUCKLEBERRY_PASSWORD in .env (see .env.example)")
        return 1

    async with aiohttp.ClientSession() as websession:
        while True:
            if creds is None:
                assert flow is not None
                creds = await flow.wait_for_login()
            client = HuckClient(creds, websession, config)
            print(f"Authenticating with Huckleberry ({creds.source})…")
            try:
                await client.connect()
            except AuthError as exc:
                print(f"✗ {exc}")
                if flow is None or creds.source == "env":
                    return 1
                credentials.clear()  # back to the sign-in page
                creds = None
                continue
            break
        print(f"Connected. Logging events for: {client.child_name}\n")
        if creds.source == "file" and client.refresh_token and client.refresh_token != creds.refresh_token:
            creds.refresh_token = client.refresh_token
            credentials.save(creds)
        if flow is not None:
            flow.set_running(client.child_name, can_sign_out=creds.source == "file")
            led.set_setup(None)
        if input_mode == "keyboard" and not sys.stdin.isatty():
            # Dev run under a preview server: no keys to read, just keep the web page up.
            print("No terminal for keyboard input; serving the web page only (Ctrl-C / SIGTERM to stop).")
            await _wait_for_stop()
            if flow is not None:
                await flow.close()
            return 0

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
            if flow is not None:
                await flow.close()
            led.close()
    print("Bye.")
    return 0


def run() -> None:
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("huckdeck.setup").setLevel(logging.INFO)
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    run()
