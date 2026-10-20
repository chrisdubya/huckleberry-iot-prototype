"""Render every display screen with made-up data — no account or hardware.

    uv run --extra display python -m huckdeck.dev_display [out.png]

Writes a contact sheet of the ribbon display's screens (default
display_preview.png) for judging layouts.
"""

from __future__ import annotations

import io
import sys

from .feedback.display import DeckState, Overlay, crop_strip, render, render_session, render_strip, session_timers, ticker_items
from .feedback.display_sim import to_png

NOW = 1_800_000_000.0

IDLE = DeckState(
    last_feed_start=NOW - 134 * 60,
    last_feed_kind="nursing",
    last_feed_detail="12m L",
    last_diaper_start=NOW - 48 * 60,
    last_diaper_mode="pee",
    diapers_today=6,
    nursing_count_today=8,
    nursing_seconds_today=2 * 3600 + 35 * 60,
)
BOTTLE = DeckState(
    last_feed_start=NOW - 25 * 60,
    last_feed_kind="bottle",
    last_feed_detail="120ml",
    last_diaper_start=NOW - 3 * 3600 - 5 * 60,
    last_diaper_mode="both",
)
NURSING = DeckState(
    nursing_active=True,
    nursing_side="left",
    nursing_banked=200,
    nursing_segment_start=NOW - 262,
    last_diaper_start=NOW - 48 * 60,
    last_diaper_mode="poo",
)
ASLEEP = DeckState(
    sleep_start=NOW - 62 * 60,
    last_feed_start=NOW - 75 * 60,
    last_feed_kind="nursing",
    last_feed_detail="18m R",
    last_diaper_start=NOW - 80 * 60,
    last_diaper_mode="pee",
)

SCENES = [
    ("idle, last feed was nursing", IDLE, None),
    ("idle, last feed was a bottle", BOTTLE, None),
    ("nursing in progress", NURSING, None),
    ("asleep", ASLEEP, None),
    ("nothing logged yet", DeckState(), None),
    ("button pressed", IDLE, Overlay("POOP DIAPER", "logging...")),
    ("logged", IDLE, Overlay("POOP DIAPER", "3:12 AM", mark="check")),
    ("nursing started", IDLE, Overlay("NURSING STARTED", "3:14 AM", mark="check")),
    ("retrying", IDLE, Overlay("BOTTLE", "no connection - retrying")),
    ("lost", IDLE, Overlay("BOTTLE", "NOT LOGGED", mark="cross")),
    ("started from the phone", IDLE, Overlay("SLEEP STARTED", "from the app")),
]


def ticker_scenes() -> list[tuple[str, object]]:
    """Windows into the scrolling ticker, plus the session timers that replace it."""
    strip = render_strip(ticker_items(IDLE, NOW))
    scenes = [(f"ticker, {offset}px in (strip is {strip.width}px)", crop_strip(strip, offset)) for offset in range(0, strip.width, 230)]
    scenes.append(("nursing in progress (replaces the ticker)", render_session(session_timers(NURSING, NOW), NOW)))
    scenes.append(("asleep (replaces the ticker)", render_session(session_timers(ASLEEP, NOW), NOW)))
    return scenes


def main() -> None:
    from PIL import Image, ImageDraw, ImageFont

    out = sys.argv[1] if len(sys.argv) > 1 else "display_preview.png"
    frames = [Image.open(io.BytesIO(to_png(frame))) for _, frame in ticker_scenes()]
    frames += [Image.open(io.BytesIO(to_png(render(state, NOW, overlay)))) for _, state, overlay in SCENES]
    cols, pad, caption_h = 2, 24, 26
    rows = -(-len(frames) // cols)
    cell_w, cell_h = frames[0].width + pad, frames[0].height + caption_h + pad
    sheet = Image.new("RGB", (cols * cell_w + pad, rows * cell_h + pad), "#1b1b1d")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=16)
    captions = [caption for caption, _ in ticker_scenes()] + [caption for caption, _, _ in SCENES]
    for i, (frame, caption) in enumerate(zip(frames, captions)):
        x, y = pad + (i % cols) * cell_w, pad + (i // cols) * cell_h
        draw.text((x, y), caption, font=font, fill="#bbbbbb")
        sheet.paste(frame, (x, y + caption_h))
    sheet.save(out)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
