# Hardware

## Parts (~$55)

| Item | Notes | ~Price |
|------|-------|--------|
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
| Slow pulsing red / green | Sleep / nursing session in progress |
| Slow fade red↔green | Both sessions active at once (shouldn't happen) |
| Fast red blink | Retrying a failed send |
| Solid red, 5s | Event lost after all retries |
| Pulsing blue | Setup mode: hotspot up, waiting for a phone (see pi-setup.md) |
| Fast blue blink | Setup mode: joining the chosen Wi-Fi |
| Solid blue, 3s | Setup mode: joined Wi-Fi |
| Dim solid blue | On Wi-Fi, waiting for Huckleberry sign-in |

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

## Case (`case/huckdeck_case.scad`)

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
