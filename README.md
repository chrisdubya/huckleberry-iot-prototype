# huckdeck

A stream-deck-style button pad for logging baby events to
[Huckleberry](https://huckleberrycare.com/) — press a physical button at 3am
instead of fumbling with a phone. This repo is the software: a terminal
prototype now, the same code on a Raspberry Pi with real buttons later.

Uses the unofficial [`huckleberry-api`](https://github.com/Woyken/py-huckleberry-api)
library, which talks to Huckleberry's Firebase backend the same way the mobile
app does. **Unofficial and reverse-engineered — not affiliated with Huckleberry
Labs; use at your own risk.**

## Setup

Requires [uv](https://docs.astral.sh/uv/) (manages Python 3.14 automatically).

```sh
uv sync
cp .env.example .env   # then fill in your Huckleberry email/password
```

Credentials live only in `.env`, which is gitignored.

## Run

```sh
uv run huckdeck
```

Keys act as the deck buttons (edit `config.yaml` to remap or change bottle
defaults):

| Key | Event |
|-----|-------|
| 1 | Pee diaper |
| 2 | Poop diaper |
| 3 | Both |
| 4 | Bottle (default 120ml formula) |
| 5 | Sleep start / stop |
| 6 | Nursing start / stop |
| q | Quit |

Events are timestamped at the moment of the press and sent in the background
with retries, so a WiFi hiccup doesn't lose or re-time anything.

Sleep and nursing stay in sync with the Huckleberry app in both directions:
the deck listens for timer changes, so a session started or stopped on a phone
shows up here (and on the LED) within a second or two, and each toggle press
checks the live timer before sending — so the button always stops a running
session and starts one only when none is running, wherever it was started. The
last known state is also cached in `~/.huckdeck.state.json` so the LED is right
immediately after a restart.

## Physical device (Raspberry Pi Zero 2 W)

The same package runs on the Pi with six 24mm arcade buttons, an RGB
status LED (green at startup = running, double-blink in the pressed button's
color = logged, blinking red = retrying, pulsing red = sleep in progress,
pulsing green = nursing in progress, fading red/green if both are active)
and, in v2, a 3.12" 256×64 OLED ribbon display on the angled face behind
the buttons:

- Parts list and wiring (buttons, LED, display): [docs/hardware.md](docs/hardware.md)
- Flash + provision + systemd service: [docs/pi-setup.md](docs/pi-setup.md)
- 3D-printable case, v2 (Blender script, STLs alongside):
  [case/huckdeck_case_v2.py](case/huckdeck_case_v2.py) — base + display
  plate, reusing the v1 top plate. v1 (no display) is
  [case/huckdeck_case.scad](case/huckdeck_case.scad).

On the Pi it runs `huckdeck --input gpio` via the systemd unit in
[deploy/](deploy/). To test the GPIO layer without hardware:

```sh
uv run --extra pi python -m huckdeck.dev_mocktest
```

### Display

A single line scrolls continuously (nothing sits still, so the OLED doesn't
burn in): the latest diaper and today's diaper count, then the latest
nursing session and today's nursing total — "today" runs 8am to 8am. Event
types are emoji (💧 pee, 💩 poop, 🤱 nursing, 😴 sleep, 🍼 bottle). While a
nursing or sleep session is being timed, the ticker is replaced by one big
live timer. A button press shows the event's name, then a check mark and
the time once Huckleberry has it. The panel runs at full brightness for a
minute after a press and dims the rest of the time.

Everything is tunable under `display:` in `config.yaml` (brightness, dim
level and delay, scroll speed, day start, `mode: panes` for a static
layout). On a laptop, `huckdeck --display sim` serves a live preview of the
panel at http://localhost:8765/, and

```sh
uv run --extra display python -m huckdeck.dev_display
```

renders every screen with made-up data to `display_preview.png`. To check a
freshly wired panel on the Pi without an account:

```sh
uv run --extra pi python -m huckdeck.dev_oledtest
```
