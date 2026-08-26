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
with retries, so a WiFi hiccup doesn't lose or re-time anything. Sleep and
nursing toggle state survives restarts (`~/.huckdeck.state.json`).

## Hardware roadmap

- **Raspberry Pi Zero 2 W** running this same package; `huckleberry-api` is on
  [piwheels](https://www.piwheels.org/project/huckleberry-api/).
- `src/huckdeck/inputs/gpio.py` (gpiozero buttons) as a drop-in replacement for
  the keyboard input — the dispatcher/client layers don't change.
- 3D-printed case, six mechanical switches, per-button status LED.
