"""Per-device identity: the hotspot name and password printed on the sticker.

Generated once on first run and stored next to the deck state file, so the
sticker can be printed after the first boot with `huckdeck --identity`.
The name ends in a short code derived from the Pi's serial number so two
devices in one house don't collide.
"""

from __future__ import annotations

import hashlib
import json
import platform
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path

IDENTITY_PATH = Path.home() / ".huckdeck.identity.json"

# No 0/O, 1/I/l: it has to be read off a sticker at 3am.
_PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
_PASSWORD_LENGTH = 10  # WPA2 minimum is 8


@dataclass(frozen=True)
class Identity:
    ssid: str
    password: str

    @property
    def wifi_qr_payload(self) -> str:
        """Standard Wi-Fi QR string: scanning it joins the phone to the hotspot."""
        return f"WIFI:T:WPA;S:{_qr_escape(self.ssid)};P:{_qr_escape(self.password)};;"


def _qr_escape(value: str) -> str:
    for ch in ("\\", ";", ",", ":", '"'):
        value = value.replace(ch, "\\" + ch)
    return value


def device_serial() -> str:
    """Raspberry Pi serial, or a stable stand-in on other machines."""
    dt = Path("/sys/firmware/devicetree/base/serial-number")
    if dt.is_file():
        return dt.read_bytes().rstrip(b"\x00").decode("ascii", "replace")
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        for line in cpuinfo.read_text().splitlines():
            if line.lower().startswith("serial"):
                return line.split(":", 1)[1].strip()
    return hashlib.sha256(platform.node().encode()).hexdigest()


def _short_code(serial: str) -> str:
    return hashlib.sha256(serial.encode()).hexdigest()[-4:]


def load_or_create(path: Path = IDENTITY_PATH) -> Identity:
    if path.is_file():
        data = json.loads(path.read_text())
        return Identity(ssid=data["ssid"], password=data["password"])
    identity = Identity(
        ssid=f"huckdeck-{_short_code(device_serial())}",
        password="".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(_PASSWORD_LENGTH)),
    )
    path.write_text(json.dumps(asdict(identity), indent=2) + "\n")
    path.chmod(0o600)
    return identity
