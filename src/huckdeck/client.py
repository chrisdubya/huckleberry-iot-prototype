"""Thin wrapper around the unofficial huckleberry-api client.

Owns authentication and child selection; exposes one method per deck event.
Everything here is async and safe to call repeatedly — ensure_session() keeps
the Firebase token fresh across long overnight runs.

Sleep/nursing sessions can also be started or stopped from the phone app, so
the timer state is readable here two ways: session_active() pulls it on
demand, and watch_sessions() streams changes from Firestore as they happen.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Callable

import aiohttp
from huckleberry_api import HuckleberryAPI

_LOGGER = logging.getLogger(__name__)

KEEPALIVE_SECONDS = 120  # well inside the library's 5-min-before-expiry refresh window

# Session kind -> Firestore collection holding its live timer document.
SESSION_COLLECTIONS = {"sleep": "sleep", "nursing": "feed"}

# on_change(kind, active), kind ∈ SESSION_COLLECTIONS; always called on the event loop.
SessionCallback = Callable[[str, bool], None]


class HuckClient:
    def __init__(
        self,
        email: str,
        password: str,
        timezone: str,
        websession: aiohttp.ClientSession,
        config: dict[str, Any],
    ) -> None:
        self._api = HuckleberryAPI(
            email=email, password=password, timezone=timezone, websession=websession
        )
        self._config = config
        self.child_uid: str = ""
        self.child_name: str = ""
        self._fresh_lock = asyncio.Lock()
        self._on_session_change: SessionCallback | None = None

    async def connect(self) -> None:
        """Authenticate and resolve which child to log against."""
        await self._api.authenticate()
        user = await self._api.get_user()
        if user is None or not user.childList:
            raise RuntimeError("No children found on this Huckleberry account")

        pinned = (self._config.get("child_uid") or "").strip()
        if pinned:
            match = next((c for c in user.childList if c.cid == pinned), None)
            if match is None:
                raise RuntimeError(f"child_uid {pinned!r} from config.yaml not found on account")
        else:
            if len(user.childList) > 1:
                _LOGGER.warning(
                    "Account has %d children; using the first. Pin one via child_uid in config.yaml: %s",
                    len(user.childList),
                    ", ".join(f"{c.nickname or '?'}={c.cid}" for c in user.childList),
                )
            match = user.childList[0]

        self.child_uid = match.cid
        self.child_name = match.nickname or match.cid

    async def _fresh(self) -> None:
        # A token refresh tears down and recreates the listeners; serialize it
        # so the keep-alive and a button press can't both do that at once.
        async with self._fresh_lock:
            await self._api.ensure_session()

    # -- live session state ---------------------------------------------

    async def session_active(self, kind: str) -> bool:
        """Pull whether a sleep/nursing timer is running right now (paused counts)."""
        await self._fresh()
        # The library has no public timer read, so go through its Firestore client.
        firestore = await self._api._get_firestore_client()  # noqa: SLF001
        doc = await firestore.collection(SESSION_COLLECTIONS[kind]).document(self.child_uid).get(timeout=10.0)
        timer = (doc.to_dict() or {}).get("timer") if doc.exists else None
        return bool(timer and timer.get("active"))

    async def watch_sessions(self, on_change: SessionCallback) -> None:
        """Stream sleep/nursing timer changes made anywhere (app, another deck).

        Fires once per timer with the current state as soon as the listener
        connects, then on every change.
        """
        self._on_session_change = on_change
        await self._fresh()
        await self._start_listeners()

    async def _start_listeners(self) -> None:
        loop = asyncio.get_running_loop()
        on_change = self._on_session_change
        assert on_change is not None

        def forward(kind: str) -> Callable[[Any], None]:
            def on_doc(doc: Any) -> None:
                # Runs on a Firestore watch thread — hop back to the event loop.
                active = bool(doc.timer and doc.timer.active)
                try:
                    loop.call_soon_threadsafe(on_change, kind, active)
                except RuntimeError:  # loop already closed during shutdown
                    pass

            return on_doc

        await self._api.setup_sleep_listener(self.child_uid, forward("sleep"))
        await self._api.setup_feed_listener(self.child_uid, forward("nursing"))

    def _listeners_healthy(self) -> bool:
        watches = list(self._api._listeners.values())  # noqa: SLF001
        return len(watches) == len(SESSION_COLLECTIONS) and all(
            getattr(watch, "is_active", True) for watch in watches
        )

    async def keep_alive(self) -> None:
        """Background task: keep the token fresh and the listeners connected.

        Button presses refresh the token on their own, but overnight there may
        be none for hours — without this the listeners go deaf when the token
        expires, or stay dead after a WiFi drop outlasts Firestore's own retries.
        """
        while True:
            await asyncio.sleep(KEEPALIVE_SECONDS)
            try:
                await self._fresh()
                if self._on_session_change is not None and not self._listeners_healthy():
                    _LOGGER.warning("Session listeners down; reconnecting")
                    await self._api.stop_all_listeners()
                    await self._start_listeners()
            except Exception:  # noqa: BLE001 — offline etc.; try again next tick
                _LOGGER.warning("Keep-alive tick failed; will retry", exc_info=True)

    async def close(self) -> None:
        await self._api.stop_all_listeners()

    # -- deck events ----------------------------------------------------

    async def log_diaper(self, mode: str, pressed_at: datetime) -> None:
        await self._fresh()
        await self._api.log_diaper(self.child_uid, start_time=pressed_at, mode=mode)

    async def log_bottle(self, pressed_at: datetime) -> None:
        bottle = self._config.get("bottle", {})
        await self._fresh()
        await self._api.log_bottle(
            self.child_uid,
            start_time=pressed_at,
            amount=float(bottle.get("amount", 120)),
            bottle_type=bottle.get("type", "Formula"),
            units=bottle.get("units", "ml"),
        )

    async def start_sleep(self) -> None:
        await self._fresh()
        await self._api.start_sleep(self.child_uid)

    async def complete_sleep(self) -> None:
        await self._fresh()
        await self._api.complete_sleep(self.child_uid)

    async def start_nursing(self) -> None:
        side = self._config.get("nursing", {}).get("start_side", "left")
        await self._fresh()
        await self._api.start_nursing(self.child_uid, side=side)

    async def complete_nursing(self) -> None:
        await self._fresh()
        await self._api.complete_nursing(self.child_uid)
