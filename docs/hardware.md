# Hardware

## Parts (~$55)

| Item | Notes | ~Price |
|------|-------|--------|
| Raspberry Pi Zero 2 **WH** | Pre-soldered header. Bare board ~$18 (PiShop.us, Adafruit) or an Amazon kit with genuine board + heatsink (~high $20s) | $18–30 |
| microSD 32GB, A1-rated | SanDisk / Samsung | $8 |
| 5V/2.5A PSU + micro-USB cable | Any solid 2.4A+ brick | $8 |
| 6× 24mm arcade buttons | Two colors: e.g. yellow/brown for diapers, white/blue for feed & sleep | $9 |
| 5mm diffused **common-cathode** RGB LED | + 3× 330Ω resistors | $2 |
| Female-female Dupont jumpers (20cm) | | $5 |
| 2.8mm (0.110") female spade connectors | 24mm arcade buttons take these; pre-crimped spade-to-Dupont leads exist | $5 |
| Optional: M2.5 heat-set inserts + screws ×4 | Pi mounting; hot glue also works | $4 |

## Wiring

All buttons: one terminal to the GPIO pin, the other to any ground pin.
Internal pull-ups are enabled in software — no resistors needed for buttons.

| Deck button | Event | BCM GPIO | Physical pin | Ground |
|-------------|-------|----------|--------------|--------|
| 1 | Pee | GPIO5 | 29 | 30 |
| 2 | Poop | GPIO6 | 31 | 30 |
| 3 | Both | GPIO13 | 33 | 34 |
| 4 | Bottle | GPIO19 | 35 | 34 |
| 5 | Sleep toggle | GPIO26 | 37 | 39 |
| 6 | Nursing toggle | GPIO16 | 36 | 39 |

RGB LED (common cathode — longest leg to ground):

| LED leg | Via | BCM GPIO | Physical pin |
|---------|-----|----------|--------------|
| Red | 330Ω | GPIO17 | 11 |
| Green | 330Ω | GPIO27 | 13 |
| Blue | 330Ω | GPIO22 | 15 |
| Cathode (long leg) | — | GND | 9 |

```
                    Pi Zero 2 W header (top view, USB ports down)
     3V3  1 ○ ○ 2  5V
          3 ○ ○ 4  5V
          5 ○ ○ 6  GND
          7 ○ ○ 8
     GND  9 ● ○ 10          9: LED common cathode
 R GPIO17 11 ● ○ 12
 G GPIO27 13 ● ○ 14 GND
 B GPIO22 15 ● ○ 16
         17 ○ ○ 18
         19 ○ ○ 20 GND
         21 ○ ○ 22
         23 ○ ○ 24
     GND 25 ○ ○ 26
         27 ○ ○ 28
  1 GPIO5 29 ● ● 30 GND     30: buttons 1+2 ground
  2 GPIO6 31 ● ○ 32
 3 GPIO13 33 ● ● 34 GND     34: buttons 3+4 ground
 4 GPIO19 35 ● ● 36 GPIO16  36: button 6
 5 GPIO26 37 ● ○ 38
     GND 39 ●               39: buttons 5+6 ground
```

Pin choices are remappable in `config.yaml` under `gpio:` — none of the
defaults conflict with boot straps, I2C, SPI, or UART, so those buses stay
free for future add-ons (e.g. an OLED on I2C).

## Case (`case/huckdeck_case.scad`)

Parametric OpenSCAD, two printed parts:

- **Top plate**: 2×3 grid of 24mm button holes (24.4mm default, tune
  `button_hole_d` to your printer), 5mm LED hole front-center.
- **Base**: Pi Zero mounting posts (58×23mm, M2.5), side cutout for the
  micro-USB power lead, screw bosses in the corners.

Print: PLA or PETG, 0.2mm layers, no supports (top prints face-down).
Adjust tolerances in the variables block, re-export STLs with:

```sh
openscad -o case/top.stl -D part=\"top\" case/huckdeck_case.scad
openscad -o case/base.stl -D part=\"base\" case/huckdeck_case.scad
```
