# Hardware

## Parts (~$55, plus ~$25 for the v2 display)

| Item | Notes | ~Price |
|------|-------|--------|
| v2: 3.12" 256×64 SSD1322 OLED, yellow | Generic "Ver 2.1" blue module (ACEIRMC 2-pack). Needs its jumpers moved to 4-wire SPI; see [Display](#display-v2) | $25 |
| v2: 8× M3×8 screws | 4 hold the display board to its plate, 4 hold the plate to the base | $2 |
| Raspberry Pi Zero 2 **WH** | Pre-soldered header. Bare board ~$18 (PiShop.us, Adafruit) or an Amazon kit with genuine board + heatsink (~high $20s) | $18–30 |
| microSD 32GB, A1-rated | SanDisk / Samsung | $8 |
| 5V/2.5A PSU + micro-USB cable | Any solid 2.4A+ brick | $8 |
| 6× 24mm arcade buttons | **Snap-in** Sanwa OBSF-24 clones (EG STARTS 12-pack). As built: yellow=pee, black=poop, white=both, blue=bottle, red=sleep, green=nursing | $9 |
| 5mm diffused **common-cathode** RGB LED | + 3× resistors, 330–470Ω (as built: 430Ω 1/4W) | $2 |
| Female-female Dupont jumpers (20cm) | Cut in half: female end presses onto the Pi header, cut end solders to the button | $5 |
| Heat shrink tubing, 3/16" | Insulates the soldered joints and inline resistors | $3 |
| Optional: M2.5 heat-set inserts + screws ×4 | Pi mounting; hot glue also works | $4 |

No spade connectors needed — the button lugs are soldered directly (see below).

## Wiring

Each button lead is half of a female-female Dupont jumper: the female end
presses onto a header pin, the cut end is stripped, threaded through the hole
in the button's blade lug, hooked back, and soldered. Heat-shrink each joint.
Button terminals have no polarity — either wire on either blade.

Every button gets its **own ground pin** (a Dupont socket can't share a pin,
unlike daisy-chained spades). All grounds are internally identical, so the
assignment is just bookkeeping. Internal pull-ups are enabled in software —
no resistors on buttons.

| Deck button | Color | Event | BCM GPIO | Physical pin | Ground pin |
|-------------|-------|-------|----------|--------------|------------|
| 1 | Yellow | Pee | GPIO5 | 29 | 25 |
| 2 | Black | Poop | GPIO6 | 31 | 30 |
| 3 | White | Both | GPIO13 | 33 | 34 |
| 4 | Blue | Bottle | GPIO19 | 35 | 39 |
| 5 | Red | Sleep toggle | GPIO26 | 37 | 20 |
| 6 | Green | Nursing toggle | GPIO16 | 36 | 14 |

RGB LED (common cathode — longest leg to ground). Solder a resistor inline on
each color leg (none on the cathode), heat-shrink each junction:

| LED leg | Via | BCM GPIO | Physical pin |
|---------|-----|----------|--------------|
| Red | 430Ω | GPIO17 | 11 |
| Green | 430Ω | GPIO27 | 13 |
| Blue | 430Ω | GPIO22 | 15 |
| Cathode (long leg) | — | GND | 9 |

If the colors come out shuffled after assembly, don't resolder — remap the
pin numbers under `gpio: led:` in `config.yaml` and restart the service.

What the LED means (see `src/huckdeck/feedback/led.py`):

| Signal | Meaning |
|--------|---------|
| Dim green, 3s at startup | Service is up and running |
| Off | Idle, no session in progress |
| Double-blink in a button's color | That event logged (purple stands in for the black poop button) |
| Slow pulsing red / green | Sleep / nursing session in progress — including one started from the phone app |
| Slow fade red↔green | Both sessions active at once (shouldn't happen) |
| Fast red blink | Retrying a failed send |
| Solid red, 5s | Event lost after all retries |

```
                    Pi Zero 2 W header (top view, USB ports down)
     3V3  1 ○ ○ 2  5V
          3 ○ ○ 4  5V
          5 ○ ○ 6  GND
          7 ○ ○ 8
     GND  9 ● ○ 10          9: LED common cathode
 R GPIO17 11 ● ○ 12
 G GPIO27 13 ● ● 14 GND     14: button 6 ground
 B GPIO22 15 ● ○ 16
         17 ○ ○ 18
         19 ○ ● 20 GND      20: button 5 ground
         21 ○ ○ 22
         23 ○ ○ 24
     GND 25 ● ○ 26          25: button 1 ground
         27 ○ ○ 28
  1 GPIO5 29 ● ● 30 GND     30: button 2 ground
  2 GPIO6 31 ● ○ 32
 3 GPIO13 33 ● ● 34 GND     34: button 3 ground
 4 GPIO19 35 ● ● 36 GPIO16  36: button 6
 5 GPIO26 37 ● ○ 38
     GND 39 ●               39: button 4 ground
```

Count twice before powering on: all button sockets sit in the bottom half of
the header except the grounds on 14 and 20; nothing belongs in the top three
rows (5V/3V3). Pin 1 is at the SD-card end, inner row; the square solder pad
on the underside marks it.

Pin choices are remappable in `config.yaml` under `gpio:` — none of the
defaults conflict with boot straps, I2C, SPI, or UART, so those buses stay
free for future add-ons (e.g. an OLED on I2C).

## Display (v2)

3.12" 256x64 SSD1322 OLED module, yellow, "Ver 2.1" blue board (ACEIRMC
2-pack on Amazon). Ships set to 8080 parallel; for 4-wire SPI the 0Ω jumpers
on the back must be R5 + R8 (as built: R6's resistor removed, R5 bridged with
solder, R8 left in place, R7 empty — the table is printed on the board).

| Module pin | Signal | BCM GPIO | Physical pin |
|------------|--------|----------|--------------|
| 1 | GND | — | 6 |
| 2 | VCC (3.3V) | — | 17 |
| 4 | SCLK | GPIO11 | 23 |
| 5 | SDIN (MOSI) | GPIO10 | 19 |
| 14 | DC | GPIO24 | 18 |
| 15 | RST | GPIO25 | 22 |
| 16 | CS | GPIO8 (CE0) | 24 |

Pins 6–13 unused. Pin 1 is at the "2 1" end of the header silkscreen; odd
pins are one column, even the other. SPI must be enabled
(`sudo raspi-config nonint do_spi 0`, then reboot). Pins remappable under
`gpio: display:` in `config.yaml`.

Measured for the case (calipers, as-built module):

| Dimension | mm |
|-----------|----|
| Board | 100.7 × 33.4 |
| Mounting holes | 4× Ø3.28 (M3), 94.7 × 28.7 centre-to-centre (98 × 32 outer edge to outer edge); centres 3.0 from the ends, 2.35 from the long edges |
| Glass | 1.5 from top/bottom edges, 4.66 from left edge, 6 from right (header) edge — the holes sit just outside it, so nothing wider than the hole fits around them on the panel side |
| Lit pixel area | starts ~11 from left edge, 4 from top edge; ~76.8 × 19.2 (nominal) |
| Header | right edge, front-face solder joints 1 and 3.5 from the edge, spanning 5.9–26.2 from the bottom edge — nothing can bear on the front face there |
| Thickness | 6.4 board + glass; header pins add ~8.5 behind |

## Case v3 (`case/huckdeck_case_v3.py`), ESP32 build

Blender script like v2 (or open `case/huckdeck_case_v3.blend`). One wedge,
109 × 124 mm, 35 mm tall at the front and 57 mm at the back, two printed
parts and no screws on the outside:

- **Shell** (`case/shell_v3.stl`): walls and the 10° face in one piece. Six
  button holes (36 mm pitch across, 32 mm front to back), the OLED window
  with v2's rail mount behind it, a light duct from a 4 mm hole in the face
  straight down to the board's RGB LED, the USB-C slot in the left wall.
  The STL is already face down; no supports.
- **Bottom** (`case/bottom_v3.stl`): plate recessed into the shell with four
  posts for the ESP32 expansion base. Four M3×8 button-head screws from
  underneath into the shell's corner bosses, four into the posts, four for
  the display as before.

The dev board stays on its expansion base, flat under the display, USB-C
ports at the left wall. Assemble: display onto the shell, snap the buttons
in, screw the base to the bottom plate, plug the jumpers, close.

The board dimensions in the script were **scaled from product photos, not
measured**. Check these with calipers and correct the parameters before
printing (the bottom plate alone is a quick test print):

| Parameter | Value used | What to measure |
|-----------|-----------|-----------------|
| `BASE_L`, `BASE_W` | 71.2 × 47.1 | expansion base PCB |
| `BASE_HOLE_DL`, `BASE_HOLE_DW` | 63.6 × 40.1 | mounting holes, centre to centre (and their diameter: posts assume M3) |
| `STACK_H` | 11.0 | top of the base PCB to the underside of the dev PCB |
| `DEV_USB_INSET` | 3.3 | dev PCB's USB edge, in from the base's USB edge |
| `USB_PITCH` | 11.3 | between the two USB-C ports |
| `LED_FROM_USB_EDGE`, `LED_OFF_CENTRE` | 26.9, 2.3 | RGB LED centre from the dev PCB's USB edge, and off the board's centreline (towards the "USB" port side) |
| `BUTTON_DEPTH` | 32 | button below the face, including lugs and solder |

`LED_SKIN = 0.4` closes the LED hole with two layers of the face, for a
hidden glow-through light in white or light filament.

## Case v2 (`case/huckdeck_case_v2.py`)

Blender script (run it in Blender 4.2+/5.x, or open
`case/huckdeck_case_v2.blend`); all dimensions are parameters at the top of
the script. Three printed parts, two of them new:

- **Base** (`case/base_v2.stl`): the v1 button section in front, unchanged,
  so the **v1 top plate fits as-is**; behind it an open sloped section for
  the display plate, with the Pi posts and the power slot in the rear wall.
  The rib between the sections is open across the middle: wire everything
  up outside, then lay the Pi and jumpers in.
- **Display plate** (`case/display_plate_v2.stl`): the 30° face. The OLED
  board sits glass-down on two rails along its short edges (the header
  side's rail is split to clear the solder joints) and is held by four
  M3×8 screws from behind, threaded into the plate. Assemble it on the
  bench, drop it onto the base, four more M3×8 from the top into the
  corner bosses.
- **Top plate**: the v1 part below.

Print both new parts at 0.2mm, open side / face up for the base, face
down for the plate. The base wants a little tree support under the rear
corner gussets and the power slot; the plate needs none. Overall
109 × 121 mm, 45 mm tall at the front and 73 mm at the back.

## Case v1 (`case/huckdeck_case.scad`)

Parametric OpenSCAD, two printed parts:

- **Top plate**: 2×3 grid of 24mm button holes (24.4mm default, tune
  `button_hole_d` to your printer), 5mm LED hole front-center. The 3mm plate
  thickness is within the 2–4mm range snap-in buttons clamp onto — push each
  button in from the top until its side clips click (no ring nut). Solder the
  leads *before* snapping buttons in.
- **Base**: Pi Zero mounting posts (58×23mm, M2.5), side cutout for the
  micro-USB power lead, screw bosses in the corners.

Print: PLA or PETG, 0.2mm layers, no supports (top prints face-down).
`case/huckdeck_case.3mf` is a ready-to-slice Bambu Studio project with both
parts plated and the print settings used for the as-built case.
Adjust tolerances in the variables block, re-export STLs with:

```sh
openscad -o case/top.stl -D part=\"top\" case/huckdeck_case.scad
openscad -o case/base.stl -D part=\"base\" case/huckdeck_case.scad
```
