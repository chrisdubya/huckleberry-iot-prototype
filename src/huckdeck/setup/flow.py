"""Setup-mode sequence: hotspot → user picks a network → join → fall back.

    scan nearby networks (before the hotspot takes the radio)
    bring up the hotspot, start the web server          LED: pulsing blue
    wait for the form                                    LED: pulsing blue
    drop the hotspot, join the chosen network            LED: blinking blue
      joined  → done                                     LED: solid blue
      failed  → rescan, hotspot back up, show the error  LED: red blink, then blue

Both halves share one aiohttp server; after Wi-Fi is up it keeps serving on
the home network at huckdeck.local for the Huckleberry sign-in:

    sign-in form → email/password checked live → child picker if >1 child
    → credentials (refresh token, never the password) saved → deck starts
    → status page with the child's name and a sign-out button

Phases: hotspot | joining | failed | connected | login | choose_child | running
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import aiohttp
from aiohttp import web

from .. import credentials as credentials_store
from ..client import AuthError, Child, sign_in
from ..credentials import Credentials
from .identity import Identity
from .wifi import Network, Wifi, WifiError

_LOGGER = logging.getLogger(__name__)

RESPONSE_FLUSH_SECONDS = 2.0  # let the "joining…" page reach the phone before the hotspot drops
FAILED_LED_SECONDS = 3.0
CONNECTED_LED_SECONDS = 3.0


SignIn = Callable[[str, str, str, aiohttp.ClientSession], Awaitable[tuple[str, list[Child]]]]


@dataclass
class SetupState:
    phase: str = "hotspot"
    hotspot_up: bool = False
    networks: list[Network] = field(default_factory=list)
    target_ssid: str | None = None
    connected_ssid: str | None = None
    has_internet: bool = False
    error: str | None = None
    # sign-in phase
    email: str = ""
    children: list[Child] = field(default_factory=list)
    child_name: str | None = None
    can_sign_out: bool = True


@dataclass
class _Submission:
    ssid: str
    password: str | None
    hidden: bool


class SetupFlow:
    def __init__(
        self,
        wifi: Wifi,
        led,
        identity: Identity,
        port: int = 80,
        bind: str = "0.0.0.0",
        sign_in_fn: SignIn = sign_in,
        credentials_path=credentials_store.CREDENTIALS_PATH,
    ) -> None:
        self.wifi = wifi
        self.led = led
        self.identity = identity
        self.port = port
        self._bind = bind
        self._sign_in = sign_in_fn
        self._credentials_path = credentials_path
        self.state = SetupState()
        self._submitted = asyncio.Event()
        self._pending: _Submission | None = None
        self._runner: web.AppRunner | None = None
        self._hotspot_started: float = 0.0
        self._login_done = asyncio.Event()
        self._pending_login: tuple[str, str, str, list[Child]] | None = None  # email, token, tz, children
        self._credentials: Credentials | None = None

    default_timezone = credentials_store.DEFAULT_TZ

    # -- web side ------------------------------------------------------------

    def submit(self, ssid: str, password: str | None, hidden: bool) -> None:
        self._pending = _Submission(ssid, password, hidden)
        self.state.phase = "joining"
        self.state.target_ssid = ssid
        self.state.error = None
        self._submitted.set()

    async def serve(self) -> None:
        """Start the web page without the hotspot (a deck that already has Wi-Fi)."""
        if self.state.phase == "hotspot":
            self.state.phase = "connected"
            self.state.connected_ssid = await self.wifi.current_ssid()
        await self._ensure_server()

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

    # -- sign-in phase -------------------------------------------------------

    async def wait_for_login(self) -> Credentials:
        """Serve the sign-in page on the LAN until the parent completes it."""
        self.state.phase = "login"
        self.state.error = None
        self.state.children = []
        self._pending_login = None
        self._credentials = None
        self._login_done.clear()
        self.led.set_setup("waiting_login")
        await self._ensure_server()
        port = "" if self.port == 80 else f":{self.port}"
        print(f"Waiting for Huckleberry sign-in at http://huckdeck.local{port}/")
        await self._login_done.wait()
        assert self._credentials is not None
        return self._credentials

    async def try_sign_in(self, email: str, password: str, timezone: str) -> str | None:
        """Check the login live. Returns an error message, or None when the login
        succeeded (then either the child picker is up or credentials are saved)."""
        self.state.email = email
        try:
            async with aiohttp.ClientSession() as session:
                token, children = await self._sign_in(email, password, timezone, session)
        except AuthError as exc:
            self.state.error = str(exc)
            return self.state.error
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            _LOGGER.warning("Sign-in request failed: %s", exc)
            self.state.error = "Couldn't reach Huckleberry. Is the internet up?"
            return self.state.error
        self.state.error = None
        self._pending_login = (email, token, timezone, children)
        if len(children) == 1:
            self.choose_child(children[0].cid)
        else:
            self.state.children = children
            self.state.phase = "choose_child"
        return None

    def choose_child(self, cid: str) -> str | None:
        if self._pending_login is None:
            return "Sign in first."
        email, token, timezone, children = self._pending_login
        child = next((c for c in children if c.cid == cid), None)
        if child is None:
            return "Pick a child from the list."
        creds = Credentials(
            email=email, timezone=timezone, refresh_token=token, child_uid=child.cid, child_name=child.name
        )
        credentials_store.save(creds, self._credentials_path)
        self._credentials = creds
        self._pending_login = None
        self.state.children = []
        self.state.child_name = child.name
        self.state.phase = "starting"
        self._login_done.set()
        return None

    def set_running(self, child_name: str, can_sign_out: bool = True) -> None:
        self.state.phase = "running"
        self.state.child_name = child_name
        self.state.can_sign_out = can_sign_out
        self.state.error = None

    def sign_out(self) -> None:
        """Forget the saved login and restart (systemd relaunches into the sign-in page)."""
        credentials_store.clear(self._credentials_path)
        _LOGGER.info("Signed out; restarting")
        os.kill(os.getpid(), signal.SIGTERM)
