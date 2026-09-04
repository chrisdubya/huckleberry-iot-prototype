"""Where the Huckleberry login lives on the device.

Two sources, first one wins:

- `.env` / environment: HUCKLEBERRY_EMAIL + HUCKLEBERRY_PASSWORD (+ _TZ).
  The developer path on a Mac.
- `~/.huckdeck.credentials.json`: written by the setup page. Holds the
  Firebase *refresh token* rather than the password, so the parent's
  Huckleberry password is never stored on the SD card, and changing it in
  the app doesn't break the deck until the token is revoked.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from dotenv import load_dotenv

CREDENTIALS_PATH = Path.home() / ".huckdeck.credentials.json"
DEFAULT_TZ = "America/New_York"


@dataclass
class Credentials:
    email: str
    timezone: str = DEFAULT_TZ
    password: str | None = None
    refresh_token: str | None = None
    child_uid: str | None = None
    child_name: str | None = None

    @property
    def source(self) -> str:
        return "env" if self.password else "file"


def load(path: Path = CREDENTIALS_PATH) -> Credentials | None:
    load_dotenv()
    email = os.environ.get("HUCKLEBERRY_EMAIL")
    password = os.environ.get("HUCKLEBERRY_PASSWORD")
    if email and password:
        return Credentials(email=email, password=password, timezone=os.environ.get("HUCKLEBERRY_TZ", DEFAULT_TZ))
    if path.is_file():
        try:
            data = json.loads(path.read_text())
            if data.get("email") and data.get("refresh_token"):
                return Credentials(
                    email=data["email"],
                    timezone=data.get("timezone") or DEFAULT_TZ,
                    refresh_token=data["refresh_token"],
                    child_uid=data.get("child_uid") or None,
                    child_name=data.get("child_name") or None,
                )
        except (ValueError, OSError):
            pass
    return None


def save(creds: Credentials, path: Path = CREDENTIALS_PATH) -> None:
    data = asdict(creds)
    data.pop("password", None)  # never persisted
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.chmod(0o600)
    tmp.replace(path)


def clear(path: Path = CREDENTIALS_PATH) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
