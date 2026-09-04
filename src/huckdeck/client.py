"""Thin wrapper around the unofficial huckleberry-api client.

Owns authentication and child selection; exposes one method per deck event.
Everything here is async and safe to call repeatedly — ensure_session() keeps
the Firebase token fresh across long overnight runs.

Two ways in: email + password (the .env developer path), or email + a
Firebase refresh token saved by the setup page (see credentials.py).
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import aiohttp
from huckleberry_api import HuckleberryAPI

from .credentials import Credentials

_LOGGER = logging.getLogger(__name__)


class AuthError(Exception):
    """Sign-in was rejected (bad password, revoked token, no children)."""


@dataclass(frozen=True)
class Child:
    cid: str
    name: str


async def sign_in(
    email: str, password: str, timezone: str, websession: aiohttp.ClientSession
) -> tuple[str, list[Child]]:
    """Log in once with a password; return the refresh token and the account's children."""
    api = HuckleberryAPI(email=email, password=password, timezone=timezone, websession=websession)
    try:
        await api.authenticate()
    except aiohttp.ClientResponseError as exc:
        if exc.status in (400, 401, 403):
            raise AuthError("Email or password not recognised.") from exc
        raise
    user = await api.get_user()
    children = [Child(c.cid, c.nickname or c.cid) for c in (user.childList if user else [])]
    if not children:
        raise AuthError("No children found on this Huckleberry account. Add one in the app first.")
    assert api.refresh_token
    return api.refresh_token, children


def _uid_from_id_token(id_token: str) -> str:
    """Firebase uid from the (unverified) JWT payload — we got it straight from Google."""
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        uid = data.get("user_id") or data.get("sub") or ""
    except (IndexError, ValueError):
        uid = ""
    if not uid:
        raise AuthError("Could not read the user id from the refreshed token")
    return uid


class HuckClient:
    def __init__(
        self,
        creds: Credentials,
        websession: aiohttp.ClientSession,
        config: dict[str, Any],
    ) -> None:
        self._api = HuckleberryAPI(
            email=creds.email, password=creds.password or "", timezone=creds.timezone, websession=websession
        )
        self._creds = creds
        self._config = config
        self.child_uid: str = ""
        self.child_name: str = ""

    @property
    def refresh_token(self) -> str | None:
        return self._api.refresh_token

    async def connect(self) -> None:
        """Authenticate and resolve which child to log against.

        Raises AuthError when the credentials are rejected (so the caller can
        fall back to the sign-in page) and lets network errors propagate.
        """
        try:
            if self._creds.password:
                await self._api.authenticate()
            else:
                self._api.refresh_token = self._creds.refresh_token
                await self._api.refresh_session_token()
                # authenticate() learns the uid from the login response; the
                # refresh path doesn't, and get_user() needs it.
                self._api.user_uid = _uid_from_id_token(self._api.id_token or "")
        except aiohttp.ClientResponseError as exc:
            if exc.status in (400, 401, 403):
                raise AuthError(f"Huckleberry rejected the saved login ({exc.status})") from exc
            raise
        user = await self._api.get_user()
        if user is None or not user.childList:
            raise AuthError("No children found on this Huckleberry account")

        # The child chosen on the setup page wins over config.yaml.
        pinned = (self._creds.child_uid or self._config.get("child_uid") or "").strip()
        if pinned:
            match = next((c for c in user.childList if c.cid == pinned), None)
            if match is None:
                raise AuthError(f"child_uid {pinned!r} not found on this account")
        else:
            if len(user.childList) > 1:
                _LOGGER.warning(
                    "Account has %d children; using the first. Pin one via child_uid in config.yaml: %s",
                    len(user.childList),
                    ", ".join(f"{c.nickname or '?'}={c.cid}" for c in user.childList),
                )
            match = user.childList[0]

        self.child_uid = match.cid
        self.child_name = match.nickname or match.cid

    async def _fresh(self) -> None:
        await self._api.ensure_session()

    async def log_diaper(self, mode: str, pressed_at: datetime) -> None:
        await self._fresh()
        await self._api.log_diaper(self.child_uid, start_time=pressed_at, mode=mode)

    async def log_bottle(self, pressed_at: datetime) -> None:
        bottle = self._config.get("bottle", {})
        await self._fresh()
        await self._api.log_bottle(
            self.child_uid,
            start_time=pressed_at,
            amount=float(bottle.get("amount", 120)),
            bottle_type=bottle.get("type", "Formula"),
            units=bottle.get("units", "ml"),
        )

    async def start_sleep(self) -> None:
        await self._fresh()
        await self._api.start_sleep(self.child_uid)

    async def complete_sleep(self) -> None:
        await self._fresh()
        await self._api.complete_sleep(self.child_uid)

    async def start_nursing(self) -> None:
        side = self._config.get("nursing", {}).get("start_side", "left")
        await self._fresh()
        await self._api.start_nursing(self.child_uid, side=side)

    async def complete_nursing(self) -> None:
        await self._fresh()
        await self._api.complete_nursing(self.child_uid)
