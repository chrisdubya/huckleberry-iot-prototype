"""Thin wrapper around the unofficial huckleberry-api client.

Owns authentication and child selection; exposes one method per deck event.
Everything here is async and safe to call repeatedly — ensure_session() keeps
the Firebase token fresh across long overnight runs.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import aiohttp
from huckleberry_api import HuckleberryAPI

_LOGGER = logging.getLogger(__name__)


class HuckClient:
    def __init__(
        self,
        email: str,
        password: str,
        timezone: str,
        websession: aiohttp.ClientSession,
        config: dict[str, Any],
    ) -> None:
        self._api = HuckleberryAPI(
            email=email, password=password, timezone=timezone, websession=websession
        )
        self._config = config
        self.child_uid: str = ""
        self.child_name: str = ""

    async def connect(self) -> None:
        """Authenticate and resolve which child to log against."""
        await self._api.authenticate()
        user = await self._api.get_user()
        if user is None or not user.childList:
            raise RuntimeError("No children found on this Huckleberry account")

        pinned = (self._config.get("child_uid") or "").strip()
        if pinned:
            match = next((c for c in user.childList if c.cid == pinned), None)
            if match is None:
                raise RuntimeError(f"child_uid {pinned!r} from config.yaml not found on account")
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
