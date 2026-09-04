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

Two things to know:

- **The repo must be public** for the curl and the clone to work — the Pi has
  no GitHub credentials. (Alternative for a private fork: `rsync` the working
  tree to `~/huckleberry-iot-prototype` on the Pi and run the script's steps
  from there.)
- **`sudo` prompts for your password** (current Pi OS images don't grant the
  first user passwordless sudo), so run this at an interactive SSH prompt —
  it can't run detached.

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
(see [hardware.md](hardware.md)) — the LED double-blinks in that button's
color and the event appears in
the Huckleberry app. Reboot (`sudo reboot`) and confirm the service comes
back on its own.

## Setup mode: Wi-Fi from a phone (no flashing tool needed)

For a deck that ships to someone else, skip the WiFi step in the imager.
With no saved Wi-Fi network the service boots into **setup mode**:

1. The LED pulses blue and the deck runs its own WPA2 hotspot. The name and
   password are unique per device — print them (and a Wi-Fi QR code) on a
   sticker. Get them with:

   ```sh
   ssh pi@huckdeck.local "cd huckleberry-iot-prototype && uv run huckdeck --identity"
   ```

   They're generated on first run and kept in `~/.huckdeck.identity.json`.
2. Join the hotspot from the phone's **Settings → Wi-Fi** (type the
   password). The captive-portal sheet then opens the setup page on its
   own. Joining by scanning the QR from the Camera app works too, but iOS
   leaves the camera open and is slow to show the sheet from there. If no
   sheet appears within a few seconds, browse to `http://10.42.0.1/`.
3. Pick the home network from the list (scanned before the hotspot took
   the radio) and enter its password. "Other network…" covers hidden SSIDs
   and anything not in the list. 2.4GHz only.
4. The LED blinks blue while the deck drops the hotspot and joins. Solid
   blue for a few seconds = joined; the page is now at
   `http://huckdeck.local/` on the home network. Blinking red = the join
   failed; the hotspot comes back and the page shows why.

Once on Wi-Fi the deck starts normally if `.env` exists. Until the
Huckleberry sign-in page is built, it otherwise waits with a dim blue LED
and the page reachable at `huckdeck.local`; add `.env` and restart.

How it works, and what the provision script installs for it:

- NetworkManager does the radio work through `nmcli`
  (`src/huckdeck/setup/wifi.py`). `deploy/polkit-huckdeck-networkmanager.rules`
  lets the `pi` user do that without sudo.
- `deploy/nm-dnsmasq-captive.conf` makes the hotspot's DNS answer every name
  with the deck's address, so phones' connectivity probes reach the setup
  page and trigger the captive-portal sheet.
- The page listens on port 80; `AmbientCapabilities=CAP_NET_BIND_SERVICE`
  in the unit allows that as a non-root user. `setup.port` in `config.yaml`
  changes it.
- The chosen network's password is passed to `nmcli` on its command line,
  so it's briefly visible in the process list on the Pi itself. The saved
  profile lives in `/etc/NetworkManager/system-connections/`, root-only.

Force setup mode on a deck that already has Wi-Fi (e.g. a new router) with
`huckdeck --setup`; a button gesture for this is still to come.

To try the pages on a Mac without a Pi, use the fake radio and a high port:

```sh
uv run huckdeck --setup --wifi fake --setup-port 8080
```

Open `http://localhost:8080/`. Joining anything with "bad" in the name, or
with a password containing "wrong", fails so the error path can be seen.

## Updating the code

```sh
ssh pi@huckdeck.local "cd huckleberry-iot-prototype && git pull && uv sync --extra pi && sudo systemctl restart huckdeck"
```
