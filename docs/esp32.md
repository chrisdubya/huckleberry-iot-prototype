# ESP32 build

The deck also runs as native firmware on an ESP32-S3 (or classic ESP32):
same buttons, same LED language, same ribbon display, same documents written
to Huckleberry. It is a port, not the Python package: the source lives in
[esp32/](../esp32/) and is built with ESP-IDF. Everything below the network
layer was ported module for module from `src/huckdeck/` (the file headers
say which Python file each one mirrors), and the Firestore writes were
copied field for field from `py-huckleberry-api` 0.4.3.

## What is the same, what is different

| | Raspberry Pi (Python) | ESP32 (C) |
|---|---|---|
| Buttons, debounce, retry queue, toggle logic | ✓ | ✓ same rules (`dispatcher.c`) |
| LED colours and timings | ✓ | ✓ (`led.c`) |
| Ticker / panes / session timer / overlays | Pillow | ✓ same fonts, pre-rendered (`display.c`, `gfx.c`) |
| Events written to Huckleberry | library | ✓ same documents, same `int`/`double` types, same field masks |
| iOS millisecond-timestamp fix before completing nursing | ✓ | ✓ |
| Sessions started on the phone show up on the deck | Firestore push, ~1 s | **polled** every `POLL_SECONDS` (default 5 s) |
| Today's totals (8am to 8am, every 10 min, after each event) | ✓ | ✓ |
| Token refresh / overnight keep-alive | ✓ | ✓ (the poll task refreshes) |
| Credentials | `.env` | `idf.py menuconfig`, baked into the firmware |
| State after a reboot | `~/.huckdeck.state.json` | NVS |
| Press before the clock is set | n/a | back-dated from uptime once NTP answers |

The one real functional difference is the polling: Firestore's live
listener protocol is gRPC-only, which is more than this chip wants to carry,
so the firmware fetches the three live documents (`sleep`, `feed`, `diaper`)
in one `batchGet` on a timer and reacts when an `updateTime` changes. At the
default 5 s that is one small HTTPS request every 5 s over a kept-alive
connection.

Two smaller things worth knowing:

- The Python deck uses raw millisecond values from an iOS-started nursing
  timer for the live display; the firmware normalises them, so the big timer
  is right either way.
- The daily totals query selects only the fields it counts. The "multi"
  history containers (up to 256 entries, a few hundred KB each on a
  long-used account) are read one entry at a time as the response streams
  in, so their size does not matter. Any other single response element over
  96 KB is skipped rather than parsed.

## Hardware

Any ESP32-S3 dev board works; one with PSRAM is nicer to have but not
required (the firmware was sized for ~320 KB of internal RAM: one TLS
connection, a 16 KB frame buffer, streamed JSON parsing). A classic ESP32
also works with the alternative pins below.

Everything else is the Pi parts list from [hardware.md](hardware.md):
six 24 mm arcade buttons to ground, a common-cathode RGB LED through
330–470 Ω, and the SSD1322 256×64 OLED in 4-wire SPI mode. The ESP32 is
3.3 V like the Pi's GPIO, so the wiring is identical apart from pin
numbers.

Default pins (change any of them in `menuconfig → huckdeck → Pins`):

| Signal | ESP32-S3 | ESP32 |
|---|---|---|
| Button 1 pee | GPIO4 | GPIO32 |
| Button 2 poop | GPIO5 | GPIO33 |
| Button 3 both | GPIO6 | GPIO25 |
| Button 4 bottle | GPIO7 | GPIO26 |
| Button 5 sleep | GPIO15 | GPIO27 |
| Button 6 nursing | GPIO16 | GPIO14 |
| LED red / green / blue | GPIO17 / 18 / 8 | GPIO16 / 17 / 4 |
| OLED MOSI (D1) | GPIO11 | GPIO23 |
| OLED SCLK (D0) | GPIO12 | GPIO18 |
| OLED CS | GPIO10 | GPIO5 |
| OLED DC | GPIO9 | GPIO21 |
| OLED RST | GPIO14 | GPIO22 |

Buttons use the internal pull-ups; all grounds are common. The OLED's VCC
goes to 3V3.

## Build and flash

Install [ESP-IDF 5.3 or newer](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/get-started/)
and open its environment (`. ~/esp/esp-idf/export.sh`). Then:

```sh
cd esp32
idf.py set-target esp32s3        # or esp32
idf.py menuconfig                # huckdeck → Network and account: WiFi, email, password, time zone
idf.py build
idf.py -p /dev/ttyUSB0 flash monitor
```

`menuconfig` writes `esp32/sdkconfig`, which is gitignored because it holds
the credentials. The `huckdeck` menu is the whole of `config.yaml`:

- **Network and account**: WiFi, Huckleberry login, time zone as a POSIX TZ
  string (America/New_York is `EST5EDT,M3.2.0,M11.1.0`), optional child uid,
  poll interval.
- **Events**: bottle amount/units/type, nursing start side, debounce.
- **Display**: enable, ticker or panes, brightness, dim level and delay,
  ticker speed, day start hour.
- **Pins**: the table above.

On boot the LED shows dim green for 3 s (running), the panel comes up, the
board joins WiFi, sets its clock from NTP, signs in and double-blinks green
(connected); a failed sign-in blinks red and retries every 10 s. The serial
monitor prints the same `✓ Pee diaper @ 03:12:07` lines as the terminal
prototype.

## Working on the firmware without a board

The display renderer, dispatcher, Firestore encoding and Huckleberry logic
are plain C and build on a laptop:

```sh
export IDF_PATH=~/esp/esp-idf           # for cJSON; or apt install libcjson-dev
cmake -S esp32/host -B esp32/host/build && cmake --build esp32/host/build
esp32/host/build/tests                  # scripted HTTP mock: checks every request the deck makes
esp32/host/build/preview out.pgm        # every screen with made-up data, like dev_display.py
```

The preview uses the same scenes and timestamp as `huckdeck.dev_display`,
so the two renderers can be compared side by side.

The fonts are generated, not drawn by hand: `esp32/tools/gen_fonts.py`
rasterises Pillow's bundled Aileron and the repo's NotoEmoji.ttf at every
size the layouts use into `esp32/main/fonts/fonts_data.c` (about 370 KB of
flash). Re-run it after changing font sizes in `display.c`.

## Layout of esp32/

| File | Mirrors | What |
|---|---|---|
| `main/main.c` | `main.py` | boot, tasks, status fan-out |
| `main/dispatcher.c` | `dispatcher.py` | queue, debounce, toggles, retries |
| `main/huck_api.c` | `client.py` + the library | the Huckleberry documents |
| `main/firestore.c` | google-cloud-firestore | REST get/set/merge/update/batchGet/runQuery |
| `main/firebase_auth.c` | the library | sign-in and token refresh |
| `main/display.c`, `gfx.c`, `fonts/` | `feedback/display.py` | the screens |
| `main/led.c` | `feedback/led.py` | the LED |
| `main/buttons.c` | `inputs/gpio.py` | the buttons |
| `main/ssd1322.c` | luma.oled | the panel |
| `main/http_esp.c`, `wifi.c`, `platform_esp.c` | — | ESP-IDF glue |
| `host/` | `dev_display.py` | laptop build: preview and tests |
