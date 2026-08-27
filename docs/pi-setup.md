# Raspberry Pi setup

Target: Raspberry Pi Zero 2 W(H) running the deck headless, auto-starting on
boot.

## 1. Flash the SD card

Use [Raspberry Pi Imager](https://www.raspberrypi.com/software/):

- OS: **Raspberry Pi OS Lite (64-bit)** (Trixie or later)
- In the imager's settings (gear icon / "Edit settings"):
  - hostname: `huckdeck`
  - username: `pi`, set a password
  - configure your WiFi SSID + password (2.4GHz — the Zero 2 W has no 5GHz)
  - enable SSH (password auth is fine)

Boot the Pi and wait a minute or two for first boot + WiFi join.

## 2. Provision

```sh
ssh pi@huckdeck.local
curl -fsSL https://raw.githubusercontent.com/chrisdubya/huckleberry-iot-prototype/main/deploy/provision.sh | bash
```

The script installs apt prerequisites (`python3-dev swig liblgpio-dev` — the
`lgpio` Python package builds from source on Trixie), installs `uv`, clones
this repo, and runs `uv sync --extra pi`. It then stops and asks for `.env`.

## 3. Credentials

From your Mac (never commit this file):

```sh
scp .env pi@huckdeck.local:~/huckleberry-iot-prototype/.env
```

Re-run the provision script; it installs and starts the systemd service.

## 4. Verify

```sh
ssh pi@huckdeck.local journalctl -u huckdeck -f
```

You should see "Connected. Logging events for: …". Press a wired button
(see [hardware.md](hardware.md)) — green LED flash and the event appears in
the Huckleberry app. Reboot (`sudo reboot`) and confirm the service comes
back on its own.

## Updating the code

```sh
ssh pi@huckdeck.local "cd huckleberry-iot-prototype && git pull && uv sync --extra pi && sudo systemctl restart huckdeck"
```
