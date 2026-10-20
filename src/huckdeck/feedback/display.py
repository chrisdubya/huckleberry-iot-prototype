"""Ribbon display feedback: 256x64 frames for an SSD1322-class OLED.

Two layouts, chosen by `display: mode:` in config.yaml:

  ticker — one line scrolling continuously (nothing ever sits still, so the
           OLED doesn't burn in): the latest diaper, the latest nursing
           session, then today's diaper count and today's nursing total.
           "Today" runs from the configured day start (8am). Event types are
           drawn as emoji (💧 pee, 💩 poop, 🤱 nursing, 😴 sleep, 🍼 bottle)
           from the bundled monochrome Noto Emoji font.
  panes  — static panes side by side: time since the last feed and diaper.

While a nursing or sleep session is running, both layouts are replaced by
one big live timer for it (drifting a few pixels now and then against
burn-in). A button press shows the event's name straight away, a check mark
and the time once Huckleberry has it, then back to the layout; "retrying"
while a send is retried, a cross if it's lost. The panel runs at full
brightness after a press and dims after `dim_after_seconds` without one.

Everything here is device-independent: render() returns a grayscale Pillow
image for whatever sink is attached (the browser simulator in display_sim.py
or the luma.oled device on the Pi). Pillow is only imported here, so runs
without a display never need it installed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
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
DIAPER_EMOJI = {"pee": "💧", "poo": "💩", "both": "💧💩", "dry": "DRY"}
NURSING_EMOJI, SLEEP_EMOJI, BOTTLE_EMOJI = "🤱", "😴", "🍼"
EMOJI_FONT_PATH = Path(__file__).with_name("fonts") / "NotoEmoji.ttf"  # monochrome, OFL

TICKER_FONT = 40  # one line, readable from across the room
SESSION_FONT = 44  # the live timer while a session runs
SESSION_DRIFT = (0, 1, 2, 1, 0, -1, -2, -1)  # px, one step every SESSION_DRIFT_SECONDS
SESSION_DRIFT_SECONDS = 20
TICKER_GAP_PX = 44  # between items, with a dot in the middle (drawn: the font has no bullet)
TICKER_DOT_R = 3


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
    last_feed_kind: str = ""  # "nursing" / "bottle"
    last_feed_detail: str = ""  # e.g. "12m L", "120ml"
    last_diaper_start: float | None = None
    last_diaper_mode: str = ""  # DIAPER_LABELS key
    # today's totals (since DAY_START_HOUR); None until fetched
    diapers_today: int | None = None
    nursing_count_today: int | None = None
    nursing_seconds_today: float = 0.0


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


def format_hours(seconds: float) -> str:
    minutes = int(max(seconds, 0) // 60)
    if minutes < 60:
        return f"{minutes}m"
    return f"{minutes // 60}h {minutes % 60:02d}m"


@lru_cache(maxsize=None)
def _font(size: int):
    from PIL import ImageFont

    return ImageFont.load_default(size=size)


@lru_cache(maxsize=None)
def _emoji_font(size: int):
    from PIL import ImageFont

    font = ImageFont.truetype(str(EMOJI_FONT_PATH), size)
    font.set_variation_by_axes([600])  # a bit heavier than Regular: reads better on the panel
    return font


def _is_emoji(ch: str) -> bool:
    return ord(ch) >= 0x1F000 or ord(ch) == 0xFE0F


def _segments(text: str) -> list[tuple[str, bool]]:
    """Split text into (chunk, is_emoji) runs so each gets the right font."""
    out: list[tuple[str, bool]] = []
    for ch in text:
        if ch == "\ufe0f":
            continue
        emoji = _is_emoji(ch)
        if out and out[-1][1] == emoji:
            out[-1] = (out[-1][0] + ch, emoji)
        else:
            out.append((ch, emoji))
    return out


def _text_width(draw, text: str, size: int) -> float:
    return sum(draw.textlength(chunk, font=_emoji_font(size) if emoji else _font(size)) for chunk, emoji in _segments(text))


def _draw_text(draw, x: float, cy: float, text: str, size: int, fill: int) -> float:
    """Draw mixed text/emoji left-aligned at x, vertically centred on cy; return the new x."""
    for chunk, emoji in _segments(text):
        font = _emoji_font(size) if emoji else _font(size)
        draw.text((x, cy), chunk, font=font, fill=fill, anchor="lm")
        x += draw.textlength(chunk, font=font)
    return x


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
        label = ("BOTTLE" if state.last_feed_kind == "bottle" else "NURSED") + (f" {state.last_feed_detail}" if state.last_feed_detail else "")
        panes.append((label, ago, "" if ago == "now" else "ago"))
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


def ticker_items(state: DeckState, now: float) -> list[list[tuple[str, int]]]:
    """The ticker's items, each a list of (text, fill) runs: dim labels, bright values.

    Grouped by kind: the diaper group (latest, then today's total), then the
    nursing group (latest, then today's total).
    """
    items: list[list[tuple[str, int]]] = []
    if state.last_diaper_start is not None:
        ago = format_ago(now - state.last_diaper_start)
        icon = DIAPER_EMOJI.get(state.last_diaper_mode, "DIAPER")
        items.append([(f"{icon} ", DIM), (ago, BRIGHT), ("" if ago == "now" else " ago", DIM)])
    else:
        items.append([(f"{DIAPER_EMOJI['both']} ", DIM), ("--", BRIGHT)])

    if state.diapers_today is not None:
        items.append([("TODAY ", DIM), (str(state.diapers_today), BRIGHT), (f" {DIAPER_EMOJI['both']}", DIM)])

    # then the nursing group: latest, then today's total
    if state.last_feed_start is not None:
        ago = format_ago(now - state.last_feed_start)
        icon = BOTTLE_EMOJI if state.last_feed_kind == "bottle" else NURSING_EMOJI
        detail = f"{state.last_feed_detail} " if state.last_feed_detail else ""
        items.append([(f"{icon} {detail}", DIM), (ago, BRIGHT), ("" if ago == "now" else " ago", DIM)])
    else:
        items.append([(f"{NURSING_EMOJI} ", DIM), ("--", BRIGHT)])
    if state.nursing_count_today is not None:
        n = state.nursing_count_today
        items.append([(f"TODAY {NURSING_EMOJI} ", DIM), (format_hours(state.nursing_seconds_today), BRIGHT), (", ", DIM), (str(n), BRIGHT), (" FEED" + ("" if n == 1 else "S"), DIM)])
    return items


def session_timers(state: DeckState, now: float) -> list[tuple[str, str]]:
    """(icon+label, timer) for each running session; empty when none."""
    timers = []
    if state.nursing_active:
        elapsed = state.nursing_banked
        if not state.nursing_paused and state.nursing_segment_start is not None:
            elapsed += now - state.nursing_segment_start
        label = NURSING_EMOJI
        if state.nursing_side:
            label += f" {state.nursing_side[0].upper()}"
        if state.nursing_paused:
            label += " PAUSED"
        timers.append((label, format_timer(elapsed)))
    if state.sleep_start is not None:
        timers.append((SLEEP_EMOJI, format_timer(now - state.sleep_start)))
    return timers


def render_session(timers: list[tuple[str, str]], now: float):
    """One big live timer (two side by side if both sessions somehow run)."""
    from PIL import Image, ImageDraw

    image = Image.new("L", (WIDTH, HEIGHT), 0)
    draw = ImageDraw.Draw(image)
    drift = SESSION_DRIFT[int(now // SESSION_DRIFT_SECONDS) % len(SESSION_DRIFT)]
    slot_w = WIDTH // max(len(timers), 1)
    for i, (label, value) in enumerate(timers):
        size = SESSION_FONT
        while size > 16 and _text_width(draw, f"{label} ", size) + _text_width(draw, value, size) > slot_w - 12:
            size -= 2
        total = _text_width(draw, f"{label} ", size) + _text_width(draw, value, size)
        x = i * slot_w + (slot_w - total) / 2 + drift
        x = _draw_text(draw, x, HEIGHT / 2 + drift / 2, f"{label} ", size, DIM)
        _draw_text(draw, x, HEIGHT / 2 + drift / 2, value, size, BRIGHT)
    return image


def render_strip(items: list[list[tuple[str, int]]]):
    """The whole ticker as one long image; it scrolls through the frame."""
    from PIL import Image, ImageDraw

    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    item_widths = [sum(_text_width(probe, text, TICKER_FONT) for text, _ in item) for item in items]
    width = max(1, int(sum(item_widths) + TICKER_GAP_PX * len(items)))  # a gap after every item, so the loop joins cleanly
    strip = Image.new("L", (width, HEIGHT), 0)
    draw = ImageDraw.Draw(strip)
    x = 0.0
    for item in items:
        for text, fill in item:
            x = _draw_text(draw, x, HEIGHT / 2, text, TICKER_FONT, fill)
        cx, cy = x + TICKER_GAP_PX / 2, HEIGHT / 2 + 2
        draw.ellipse((cx - TICKER_DOT_R, cy - TICKER_DOT_R, cx + TICKER_DOT_R, cy + TICKER_DOT_R), fill=DIM)
        x += TICKER_GAP_PX
    return strip


def crop_strip(strip, offset: int):
    """A 256px window into the strip at `offset`, wrapping around."""
    from PIL import Image

    width = strip.width
    offset %= width
    frame = Image.new("L", (WIDTH, HEIGHT), 0)
    frame.paste(strip.crop((offset, 0, min(offset + WIDTH, width), HEIGHT)), (0, 0))
    if offset + WIDTH > width:
        frame.paste(strip.crop((0, 0, offset + WIDTH - width, HEIGHT)), (width - offset, 0))
    return frame


def render(state: DeckState, now: float, overlay: Overlay | None = None):
    """One 256x64 grayscale frame in the panes layout."""
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

    def __init__(
        self,
        mode: str = "ticker",
        brightness: int = 80,
        dim_brightness: int = 20,
        dim_after_seconds: float = 60.0,
        ticker_speed: float = 24.0,
    ) -> None:
        self.state = DeckState()
        self.mode = mode
        self.brightness = brightness
        self.dim_brightness = dim_brightness
        self.dim_after = dim_after_seconds
        self.ticker_speed = ticker_speed  # px/s
        self._overlay: Overlay | None = None
        self._last_input = time.time()
        self._strip = None
        self._strip_key: tuple | None = None
        self._strip_width = 1
        self._scroll_start = time.time()
        self._scroll_offset = 0.0  # px into the strip at _scroll_start

    # -- brightness --------------------------------------------------------

    def current_brightness(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        return self.brightness if now - self._last_input < self.dim_after else self.dim_brightness

    def touch(self, now: float | None = None) -> None:
        """A button was pressed: back to full brightness."""
        self._last_input = time.time() if now is None else now

    # -- frames ------------------------------------------------------------

    def frame(self, now: float | None = None):
        now = time.time() if now is None else now
        if self._overlay is not None and self._overlay.until is not None and now >= self._overlay.until:
            self._overlay = None
        if self._overlay is not None:
            return render(self.state, now, self._overlay)
        timers = session_timers(self.state, now)
        if timers:
            return render_session(timers, now)  # a running session takes over the whole panel
        if self.mode != "ticker":
            return render(self.state, now)
        return self._ticker_frame(now)

    def _ticker_frame(self, now: float):
        items = ticker_items(self.state, now)
        key = tuple(tuple(item) for item in items)
        if key != self._strip_key:
            # text changed (a minute ticked over, a session started...): rebuild
            # the strip but keep the scroll position so the motion stays smooth
            self._scroll_offset = self.scroll_offset(now)
            self._scroll_start = now
            self._strip = render_strip(items)
            self._strip_key = key
            self._strip_width = self._strip.width
        return crop_strip(self._strip, int(self.scroll_offset(now)))

    def scroll_offset(self, now: float) -> float:
        return (self._scroll_offset + (now - self._scroll_start) * self.ticker_speed) % self._strip_width

    def on_event(self, status: str, action: str, detail: str) -> None:
        """Same contract as StatusLed.on_event (see dispatcher.StatusCallback)."""
        now = time.time()
        title = EVENT_LABELS.get(action, "").upper()
        match status:
            case "sending":
                self.touch(now)
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
            parts = []
            if nursing.duration:
                parts.append(_duration(float(nursing.duration)))
            side = prefs.lastSide.lastSide if prefs.lastSide else "none"
            if side != "none":
                parts.append(side[0].upper())
            self.state.last_feed_start, self.state.last_feed_kind, self.state.last_feed_detail = nursing_start, "nursing", " ".join(parts)
        else:
            detail = f"{float(bottle.bottleAmount):g}{bottle.bottleUnits or ''}" if bottle.bottleAmount else ""
            self.state.last_feed_start, self.state.last_feed_kind, self.state.last_feed_detail = bottle_start, "bottle", detail

    def set_totals(self, diapers: int, nursing_count: int, nursing_seconds: float) -> None:
        self.state.diapers_today = diapers
        self.state.nursing_count_today = nursing_count
        self.state.nursing_seconds_today = nursing_seconds

    def close(self) -> None:
        pass


class NullDisplay:
    """Stand-in when no display is configured."""

    def on_event(self, status: str, action: str, detail: str) -> None:
        pass

    def on_doc(self, collection: str, doc: Any) -> None:
        pass

    def set_totals(self, diapers: int, nursing_count: int, nursing_seconds: float) -> None:
        pass

    def close(self) -> None:
        pass
