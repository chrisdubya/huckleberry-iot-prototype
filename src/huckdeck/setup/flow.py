"""Setup-mode sequence: hotspot → user picks a network → join → fall back.

    scan nearby networks (before the hotspot takes the radio)
    bring up the hotspot, start the web server          LED: pulsing blue
    wait for the form                                    LED: pulsing blue
    drop the hotspot, join the chosen network            LED: blinking blue
      joined  → done                                     LED: solid blue
      failed  → rescan, hotspot back up, show the error  LED: red blink, then blue

Both halves share one aiohttp server; after Wi-Fi is up it keeps serving on
the home network at huckdeck.local, which is where the Huckleberry sign-in
will live next.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from aiohttp import web

from .identity import Identity
from .wifi import Network, Wifi, WifiError

_LOGGER = logging.getLogger(__name__)

RESPONSE_FLUSH_SECONDS = 2.0  # let the "joining…" page reach the phone before the hotspot drops
FAILED_LED_SECONDS = 3.0
CONNECTED_LED_SECONDS = 3.0


@dataclass
class SetupState:
    phase: str = "hotspot"  # hotspot | joining | failed | connected | waiting_login
    hotspot_up: bool = False
    networks: list[Network] = field(default_factory=list)
    target_ssid: str | None = None
    connected_ssid: str | None = None
    has_internet: bool = False
    error: str | None = None


@dataclass
class _Submission:
    ssid: str
    password: str | None
    hidden: bool


class SetupFlow:
    def __init__(self, wifi: Wifi, led, identity: Identity, port: int = 80, bind: str = "0.0.0.0") -> None:
        self.wifi = wifi
        self.led = led
        self.identity = identity
        self.port = port
        self._bind = bind
        self.state = SetupState()
        self._submitted = asyncio.Event()
        self._pending: _Submission | None = None
        self._runner: web.AppRunner | None = None
        self._hotspot_started: float = 0.0

    # -- web side ------------------------------------------------------------

    def submit(self, ssid: str, password: str | None, hidden: bool) -> None:
        self._pending = _Submission(ssid, password, hidden)
        self.state.phase = "joining"
        self.state.target_ssid = ssid
        self.state.error = None
        self._submitted.set()

    async def _ensure_server(self) -> None:
        if self._runner is not None:
            return
        from .web import make_app

        self._runner = web.AppRunner(make_app(self), access_log=None)
        await self._runner.setup()
        await web.TCPSite(self._runner, self._bind, self.port).start()
        _LOGGER.info("Setup page listening on %s:%s", self._bind, self.port)

    async def close(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        if self.state.hotspot_up:
            await self.wifi.stop_hotspot()
            self.state.hotspot_up = False

    # -- sequence ------------------------------------------------------------

    def seconds_since_hotspot_up(self) -> float:
        return time.monotonic() - self._hotspot_started if self._hotspot_started else 0.0

    async def _scan(self) -> None:
        try:
            self.state.networks = await self.wifi.scan()
        except WifiError as exc:
            _LOGGER.warning("Wi-Fi scan failed: %s", exc)
            self.state.networks = []

    async def _hotspot_up(self) -> None:
        await self.wifi.start_hotspot(self.identity.ssid, self.identity.password)
        self.state.hotspot_up = True
        self._hotspot_started = time.monotonic()
        self.led.set_setup("hotspot")
        port = "" if self.port == 80 else f":{self.port}"
        print(f"Setup mode: join Wi-Fi '{self.identity.ssid}' (password {self.identity.password}) "
              f"and open http://huckdeck.local{port}/ (or http://10.42.0.1{port}/)")

    async def run_wifi(self) -> str:
        """Run until the deck is on a Wi-Fi network; returns its SSID."""
        await self._scan()
        await self._hotspot_up()
        await self._ensure_server()

        while True:
            self._submitted.clear()
            await self._submitted.wait()
            sub = self._pending
            assert sub is not None
            self.led.set_setup("joining")
            await asyncio.sleep(RESPONSE_FLUSH_SECONDS)
            await self.wifi.stop_hotspot()
            self.state.hotspot_up = False
            try:
                await self.wifi.join(sub.ssid, sub.password, sub.hidden)
            except WifiError as exc:
                _LOGGER.warning("Join %s failed: %s", sub.ssid, exc)
                self.state.error = f"Couldn't join {sub.ssid}: {exc}"
                self.state.phase = "failed"
                self.led.set_setup("failed")
                await self._scan()
                await asyncio.sleep(max(0.0, FAILED_LED_SECONDS - RESPONSE_FLUSH_SECONDS))
                await self._hotspot_up()
                continue

            self.state.phase = "connected"
            self.state.connected_ssid = sub.ssid
            self.state.has_internet = await self.wifi.has_internet()
            self.led.set_setup("connected")
            print(f"Joined Wi-Fi '{sub.ssid}' (internet: {'yes' if self.state.has_internet else 'no'}). "
                  f"Setup page now at http://huckdeck.local{'' if self.port == 80 else f':{self.port}'}/")
            await asyncio.sleep(CONNECTED_LED_SECONDS)
            return sub.ssid

    async def wait_for_login(self) -> None:
        """Placeholder for the Huckleberry sign-in phase: keep the page up on the LAN."""
        self.state.phase = "waiting_login"
        self.led.set_setup("waiting_login")
        await self._ensure_server()
        await asyncio.Event().wait()
