"""Ribbon display feedback: a 256x64 "ticker" frame (SSD1322-class OLED).

What it shows:
  idle       — panes side by side: time since the last feed (and what it
               was), time since the last diaper (and its type)
  nursing    — the feed pane becomes a live timer for the running session
  asleep     — an extra pane with a live sleep timer
  on a press — the event's name straight away, a check mark and the time once
               Huckleberry has it, then back to the panes
  trouble    — "retrying" while a send is being retried, a cross if it's lost

Everything here is device-independent: render() returns a grayscale Pillow
image for whatever sink is attached (the browser simulator in display_sim.py
now, a luma.oled device on the Pi later). Pillow is only imported here, so
runs without a display never need it installed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from ..dispatcher import EVENT_LABELS

WIDTH, HEIGHT = 256, 64

BRIGHT = 255
DIM = 120  # labels and separators; the panel has 4-bit grayscale
FAINT = 50

SUCCESS_SECONDS = 3.0
FAILED_SECONDS = 5.0
REMOTE_SECONDS = 3.0

DIAPER_LABELS = {"pee": "PEE", "poo": "POOP", "both": "PEE+POOP", "dry": "DRY"}


@dataclass
class DeckState:
    """What the panes are drawn from. All times are epoch seconds."""

    nursing_active: bool = False
    nursing_paused: bool = False
    nursing_side: str = ""  # "left" / "right"
    nursing_banked: float = 0.0  # seconds fed before the current segment
    nursing_segment_start: float | None = None
    sleep_start: float | None = None  # set while a sleep session is running
    last_feed_start: float | None = None
    last_feed_detail: str = ""  # e.g. "NURSED 12m L", "BOTTLE 120ml"
    last_diaper_start: float | None = None
    last_diaper_mode: str = ""  # DIAPER_LABELS key


@dataclass
class Overlay:
    """A full-width message that temporarily replaces the panes."""

    title: str
    sub: str = ""
    mark: str = ""  # "check" / "cross" / ""
    until: float | None = None  # epoch seconds; None = until replaced


def format_ago(seconds: float) -> str:
    minutes = int(max(seconds, 0) // 60)
    if minutes < 1:
        return "now"
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes:02d}m"
    return f"{hours // 24}d {hours % 24}h"


def format_timer(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    if seconds < 3600:
        return f"{seconds // 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 3600}h {seconds % 3600 // 60:02d}m"


def _duration(seconds: float) -> str:
    return f"{max(round(seconds / 60), 1)}m"


@lru_cache(maxsize=None)
def _font(size: int):
    from PIL import ImageFont

    return ImageFont.load_default(size=size)


def _fit(draw, text: str, max_width: int, sizes: tuple[int, ...]):
    """Largest font from `sizes` (descending) that fits `text` in `max_width`."""
    for size in sizes:
        font = _font(size)
        if draw.textlength(text, font=font) <= max_width:
            return font
    return _font(sizes[-1])


def _panes(state: DeckState, now: float) -> list[tuple[str, str, str]]:
    """(label, value, suffix) per pane, left to right."""
    panes = []
    if state.nursing_active:
        elapsed = state.nursing_banked
        if not state.nursing_paused and state.nursing_segment_start is not None:
            elapsed += now - state.nursing_segment_start
        label = "PAUSED" if state.nursing_paused else "NURSING"
        if state.nursing_side:
            label += f" {state.nursing_side.upper()}"
        panes.append((label, format_timer(elapsed), ""))
    elif state.last_feed_start is not None:
        ago = format_ago(now - state.last_feed_start)
        panes.append((state.last_feed_detail or "FED", ago, "" if ago == "now" else "ago"))
    else:
        panes.append(("FED", "--", ""))

    if state.sleep_start is not None:
        panes.append(("ASLEEP", format_timer(now - state.sleep_start), ""))

    if state.last_diaper_start is not None:
        ago = format_ago(now - state.last_diaper_start)
        label = DIAPER_LABELS.get(state.last_diaper_mode, "DIAPER")
        panes.append((label if label == "DIAPER" else f"DIAPER {label}", ago, "" if ago == "now" else "ago"))
    else:
        panes.append(("DIAPER", "--", ""))
    return panes


def _draw_panes(draw, state: DeckState, now: float) -> None:
    panes = _panes(state, now)
    pane_w = WIDTH // len(panes)
    pad = 6
    for i, (label, value, suffix) in enumerate(panes):
        x = i * pane_w
        if i:
            draw.line([(x, 6), (x, HEIGHT - 7)], fill=FAINT)
        inner = pane_w - 2 * pad
        draw.text((x + pad, 3), label, font=_fit(draw, label, inner, (13, 12, 11, 10)), fill=DIM)
        suffix_font = _font(13)
        suffix_w = draw.textlength(f" {suffix}", font=suffix_font) if suffix else 0
        if suffix and draw.textlength(value, font=_font(22)) > inner - suffix_w:
            suffix, suffix_w = "", 0  # narrow pane: the number matters more than "ago"
        value_font = _fit(draw, value, inner - suffix_w, (34, 30, 26, 22, 18))
        draw.text((x + pad, 58), value, font=value_font, fill=BRIGHT, anchor="ls")
        if suffix:
            value_w = draw.textlength(value, font=value_font)
            draw.text((x + pad + value_w, 58), f" {suffix}", font=suffix_font, fill=DIM, anchor="ls")


def _draw_mark(draw, mark: str, x: int, cy: int) -> int:
    """Draw a check/cross with its left edge at x; return its width."""
    if mark == "check":
        draw.line([(x, cy), (x + 7, cy + 8), (x + 20, cy - 9)], fill=BRIGHT, width=4, joint="curve")
        return 20
    if mark == "cross":
        draw.line([(x, cy - 9), (x + 18, cy + 9)], fill=BRIGHT, width=4)
        draw.line([(x, cy + 9), (x + 18, cy - 9)], fill=BRIGHT, width=4)
        return 18
    return 0


def _draw_overlay(draw, overlay: Overlay) -> None:
    mark_w = {"check": 20, "cross": 18}.get(overlay.mark, 0)
    gap = 10 if mark_w else 0
    title_y = 24 if overlay.sub else HEIGHT // 2
    font = _fit(draw, overlay.title, WIDTH - 16 - mark_w - gap, (30, 26, 22, 18, 15))
    title_w = draw.textlength(overlay.title, font=font)
    x = (WIDTH - (mark_w + gap + title_w)) // 2
    _draw_mark(draw, overlay.mark, x, title_y)
    draw.text((x + mark_w + gap, title_y), overlay.title, font=font, fill=BRIGHT, anchor="lm")
    if overlay.sub:
        sub_font = _fit(draw, overlay.sub, WIDTH - 16, (14, 12, 10))
        draw.text((WIDTH // 2, 52), overlay.sub, font=sub_font, fill=DIM, anchor="mm")


def render(state: DeckState, now: float, overlay: Overlay | None = None):
    """One 256x64 grayscale frame."""
    from PIL import Image, ImageDraw

    image = Image.new("L", (WIDTH, HEIGHT), 0)
    draw = ImageDraw.Draw(image)
    if overlay is not None:
        _draw_overlay(draw, overlay)
    else:
        _draw_panes(draw, state, now)
    return image


def _clock(now: float) -> str:
    return time.strftime("%I:%M %p", time.localtime(now)).lstrip("0")


class Display:
    """Holds the deck state and the current overlay; renders frames on demand."""

    def __init__(self) -> None:
        self.state = DeckState()
        self._overlay: Overlay | None = None

    def frame(self, now: float | None = None):
        now = time.time() if now is None else now
        if self._overlay is not None and self._overlay.until is not None and now >= self._overlay.until:
            self._overlay = None
        return render(self.state, now, self._overlay)

    def on_event(self, status: str, action: str, detail: str) -> None:
        """Same contract as StatusLed.on_event (see dispatcher.StatusCallback)."""
        now = time.time()
        title = EVENT_LABELS.get(action, "").upper()
        match status:
            case "sending":
                self._overlay = Overlay(title, "logging...")
            case "success":
                self._overlay = Overlay(title, _clock(now), mark="check", until=now + SUCCESS_SECONDS)
            case "retrying":
                self._overlay = Overlay(title, "no connection - retrying")
            case "failed":
                self._overlay = Overlay(title, "NOT LOGGED", mark="cross", until=now + FAILED_SECONDS)
            case "remote":
                self._overlay = Overlay(title, "from the app", until=now + REMOTE_SECONDS)
            case "ignored":
                pass

    def on_doc(self, collection: str, doc: Any) -> None:
        """Take a Firestore root document pushed by HuckClient's listeners."""
        state = self.state
        timer = getattr(doc, "timer", None)
        prefs = getattr(doc, "prefs", None)
        if collection == "sleep":
            running = bool(timer and timer.active and timer.timerStartTime)
            state.sleep_start = float(timer.timerStartTime) / 1000 if running else None  # sleep timer is in ms
        elif collection == "diaper":
            last = prefs.lastDiaper if prefs else None
            if last is not None and last.start is not None:
                state.last_diaper_start = float(last.start)
                state.last_diaper_mode = last.mode or ""
        elif collection == "feed":
            state.nursing_active = bool(timer and timer.active)
            if state.nursing_active:
                state.nursing_paused = bool(timer.paused)
                side = timer.activeSide or timer.lastSide or ""
                state.nursing_side = "" if side == "none" else side
                state.nursing_banked = float(timer.leftDuration or 0) + float(timer.rightDuration or 0)
                start = timer.timerStartTime
                state.nursing_segment_start = float(start) if start else None
            self._set_last_feed(prefs)

    def _set_last_feed(self, prefs: Any) -> None:
        nursing = prefs.lastNursing if prefs else None
        bottle = prefs.lastBottle if prefs else None
        nursing_start = float(nursing.start) if nursing and nursing.start is not None else None
        bottle_start = float(bottle.start) if bottle and bottle.start is not None else None
        if nursing_start is None and bottle_start is None:
            return
        if bottle_start is None or (nursing_start is not None and nursing_start >= bottle_start):
            detail = "NURSED"
            if nursing.duration:
                detail += f" {_duration(float(nursing.duration))}"
            side = prefs.lastSide.lastSide if prefs.lastSide else "none"
            if side != "none":
                detail += f" {side[0].upper()}"
            self.state.last_feed_start, self.state.last_feed_detail = nursing_start, detail
        else:
            detail = "BOTTLE"
            if bottle.bottleAmount:
                detail += f" {float(bottle.bottleAmount):g}{bottle.bottleUnits or ''}"
            self.state.last_feed_start, self.state.last_feed_detail = bottle_start, detail

    def close(self) -> None:
        pass


class NullDisplay:
    """Stand-in when no display is configured."""

    def on_event(self, status: str, action: str, detail: str) -> None:
        pass

    def on_doc(self, collection: str, doc: Any) -> None:
        pass

    def close(self) -> None:
        pass
