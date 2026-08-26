"""Press-to-event dispatcher: debounce, toggle state, queue, retry.

The input layer calls press() with an event name; the timestamp is captured
here, at press time. Network sends happen on a background consumer task with
retries so a WiFi blip never loses or re-times an event.

Sleep/nursing toggle state persists to a JSON file so a restart mid-session
still completes the session on the next press.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from .client import HuckClient

_LOGGER = logging.getLogger(__name__)

RETRIES = 3
BACKOFF_BASE_SECONDS = 2  # 2s, 4s, 8s

EVENT_LABELS = {
    "pee": "Pee diaper",
    "poo": "Poop diaper",
    "both": "Pee+poop diaper",
    "bottle": "Bottle",
    "sleep_start": "Sleep started",
    "sleep_stop": "Sleep completed",
    "nursing_start": "Nursing started",
    "nursing_stop": "Nursing completed",
}


@dataclass
class Event:
    action: str  # key of EVENT_LABELS
    pressed_at: datetime


class Dispatcher:
    def __init__(
        self,
        client: HuckClient,
        state_path: Path,
        debounce_seconds: float,
        on_status: Callable[[str], None],
    ) -> None:
        self._client = client
        self._state_path = state_path
        self._debounce = debounce_seconds
        self._on_status = on_status
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._last_press: dict[str, datetime] = {}
        self._state = self._load_state()

    # -- toggle state ---------------------------------------------------

    def _load_state(self) -> dict:
        try:
            return json.loads(self._state_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {"sleep_active": False, "nursing_active": False}

    def _save_state(self) -> None:
        self._state_path.write_text(json.dumps(self._state))

    @property
    def sleep_active(self) -> bool:
        return self._state["sleep_active"]

    @property
    def nursing_active(self) -> bool:
        return self._state["nursing_active"]

    # -- input side -----------------------------------------------------

    def press(self, event_name: str) -> None:
        """Called by the input layer. Resolves toggles and enqueues."""
        now = datetime.now()
        last = self._last_press.get(event_name)
        if last is not None and (now - last).total_seconds() < self._debounce:
            self._on_status(f"(ignored double-press: {event_name})")
            return
        self._last_press[event_name] = now

        if event_name == "sleep_toggle":
            action = "sleep_stop" if self.sleep_active else "sleep_start"
            self._state["sleep_active"] = not self.sleep_active
            self._save_state()
        elif event_name == "nursing_toggle":
            action = "nursing_stop" if self.nursing_active else "nursing_start"
            self._state["nursing_active"] = not self.nursing_active
            self._save_state()
        else:
            action = event_name

        self._on_status(f"⏳ {EVENT_LABELS[action]} — sending…")
        self._queue.put_nowait(Event(action=action, pressed_at=now))

    # -- network side ---------------------------------------------------

    async def wait_idle(self) -> None:
        """Block until every queued event has been sent (or given up on)."""
        await self._queue.join()

    async def run(self) -> None:
        """Consumer loop. Run as a background task for the app's lifetime."""
        while True:
            event = await self._queue.get()
            try:
                await self._send_with_retry(event)
            finally:
                self._queue.task_done()

    async def _send_with_retry(self, event: Event) -> None:
        label = EVENT_LABELS[event.action]
        stamp = event.pressed_at.strftime("%H:%M:%S")
        for attempt in range(1, RETRIES + 1):
            try:
                await self._send(event)
                self._on_status(f"✓ {label} @ {stamp}")
                return
            except Exception as exc:  # noqa: BLE001 — any network/API failure retries
                if attempt < RETRIES:
                    delay = BACKOFF_BASE_SECONDS**attempt
                    self._on_status(f"⚠ {label} failed ({exc}); retry {attempt}/{RETRIES - 1} in {delay}s")
                    await asyncio.sleep(delay)
                else:
                    _LOGGER.exception("Giving up on %s pressed at %s", event.action, stamp)
                    self._on_status(f"✗ {label} @ {stamp} LOST after {RETRIES} attempts: {exc}")

    async def _send(self, event: Event) -> None:
        match event.action:
            case "pee" | "poo" | "both":
                await self._client.log_diaper(event.action, event.pressed_at)
            case "bottle":
                await self._client.log_bottle(event.pressed_at)
            case "sleep_start":
                await self._client.start_sleep()
            case "sleep_stop":
                await self._client.complete_sleep()
            case "nursing_start":
                await self._client.start_nursing()
            case "nursing_stop":
                await self._client.complete_nursing()
            case _:
                raise ValueError(f"Unknown action {event.action!r}")
