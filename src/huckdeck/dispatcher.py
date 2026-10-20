"""Press-to-event dispatcher: debounce, toggle state, queue, retry.

The input layer calls press() with an event name; the timestamp is captured
here, at press time. Network sends happen on a background consumer task with
retries so a WiFi blip never loses or re-times an event.

Sleep/nursing sessions can also be started or stopped from the phone app, so
Huckleberry — not this process — owns the truth about whether one is running:

- press() guesses start vs. stop from the local state for instant feedback;
- before sending, the consumer pulls the real timer state and corrects the
  guess, so a press always toggles what the server actually has;
- sync_remote() takes pushed timer changes from the client's listeners so the
  LED follows sessions started or stopped elsewhere.

The local state still persists to a JSON file so the LED is right immediately
after a restart, before the listeners have connected.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
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

# Toggle button event -> session kind.
TOGGLES = {"sleep_toggle": "sleep", "nursing_toggle": "nursing"}


@dataclass
class Event:
    action: str  # key of EVENT_LABELS
    pressed_at: datetime
    kind: str | None = None  # "sleep"/"nursing" for toggle presses
    resolved: bool = False  # toggle action confirmed against the server


# Status callback contract: on_event(status, action, detail).
# status ∈ {"sending", "success", "retrying", "failed", "ignored", "remote"};
# action is an EVENT_LABELS key ("" for ignored); detail is display text.
# "remote" reports a session started/stopped somewhere other than this deck.
StatusCallback = Callable[[str, str, str], None]


class Dispatcher:
    def __init__(
        self,
        client: HuckClient,
        state_path: Path,
        debounce_seconds: float,
        on_event: StatusCallback,
    ) -> None:
        self._client = client
        self._state_path = state_path
        self._debounce = debounce_seconds
        self._on_event = on_event
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._last_press: dict[str, datetime] = {}
        self._state = self._load_state()
        # Last state known to be on the server, and toggle presses still in
        # flight. While a press is in flight the local state is an optimistic
        # guess; once the last one lands it settles back to the server's.
        self._remote = {kind: self._state[f"{kind}_active"] for kind in TOGGLES.values()}
        self._pending: Counter[str] = Counter()

    # -- toggle state ---------------------------------------------------

    def _load_state(self) -> dict:
        try:
            return json.loads(self._state_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {"sleep_active": False, "nursing_active": False}

    def _set_active(self, kind: str, active: bool) -> None:
        if self._state[f"{kind}_active"] != active:
            self._state[f"{kind}_active"] = active
            self._state_path.write_text(json.dumps(self._state))

    def sync_remote(self, kind: str, active: bool) -> None:
        """Called (on the event loop) when the server's timer state changes."""
        self._remote[kind] = active
        if self._pending[kind] or self._state[f"{kind}_active"] == active:
            return  # our own press is in flight / echo of what we already know
        self._set_active(kind, active)
        action = f"{kind}_start" if active else f"{kind}_stop"
        self._on_event("remote", action, f"{EVENT_LABELS[action]} (synced from Huckleberry)")

    def _settle(self, kind: str) -> None:
        self._pending[kind] -= 1
        if not self._pending[kind]:
            self._set_active(kind, self._remote[kind])

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
            self._on_event("ignored", "", f"double-press: {event_name}")
            return
        self._last_press[event_name] = now

        kind = TOGGLES.get(event_name)
        if kind is not None:
            active = self._state[f"{kind}_active"]
            action = f"{kind}_stop" if active else f"{kind}_start"
            self._pending[kind] += 1
            self._set_active(kind, not active)
        else:
            action = event_name

        self._on_event("sending", action, EVENT_LABELS[action])
        self._queue.put_nowait(Event(action=action, pressed_at=now, kind=kind))

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

    async def _resolve_toggle(self, event: Event) -> None:
        """Let the server's timer state, not our guess, pick start vs. stop.

        Only until the first successful read: if a write then times out after
        actually landing, re-resolving on the retry would undo it.
        """
        assert event.kind is not None
        active = await self._client.session_active(event.kind)
        event.resolved = True
        self._remote[event.kind] = active
        action = f"{event.kind}_stop" if active else f"{event.kind}_start"
        if action != event.action:
            event.action = action
            self._set_active(event.kind, not active)
            was = "already running" if active else "not running"
            self._on_event("sending", action, f"{EVENT_LABELS[action]} (session was {was} in Huckleberry)")

    async def _send_with_retry(self, event: Event) -> None:
        stamp = event.pressed_at.strftime("%H:%M:%S")
        for attempt in range(1, RETRIES + 1):
            try:
                if event.kind is not None and not event.resolved:
                    await self._resolve_toggle(event)
                await self._send(event)
                if event.kind is not None:
                    self._remote[event.kind] = event.action.endswith("_start")
                    self._settle(event.kind)
                self._on_event("success", event.action, f"{EVENT_LABELS[event.action]} @ {stamp}")
                return
            except Exception as exc:  # noqa: BLE001 — any network/API failure retries
                label = EVENT_LABELS[event.action]
                if attempt < RETRIES:
                    delay = BACKOFF_BASE_SECONDS**attempt
                    self._on_event(
                        "retrying", event.action,
                        f"{label} failed ({exc}); retry {attempt}/{RETRIES - 1} in {delay}s",
                    )
                    await asyncio.sleep(delay)
                else:
                    _LOGGER.exception("Giving up on %s pressed at %s", event.action, stamp)
                    if event.kind is not None:
                        self._settle(event.kind)  # drop the optimistic guess
                    self._on_event(
                        "failed", event.action,
                        f"{label} @ {stamp} LOST after {RETRIES} attempts: {exc}",
                    )

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
