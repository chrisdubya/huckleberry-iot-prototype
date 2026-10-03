"""Read-only dump of the nursing/feed state in your Huckleberry account.

Prints the live feed timer, the "last nursing" prefs the app's Live Activity
reads, the most recent feed interval records, and the sleep timer — each time
field annotated with its apparent unit (seconds vs milliseconds) and the
wall-clock date it decodes to. Nothing is written.

    uv run python -m huckdeck.diag_feed            # newest 25 feed intervals
    uv run python -m huckdeck.diag_feed -n 60      # more history
    uv run python -m huckdeck.diag_feed --raw      # also print raw documents

Needs the same .env as huckdeck (HUCKLEBERRY_EMAIL / HUCKLEBERRY_PASSWORD).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp
import yaml
from dotenv import load_dotenv
from google.cloud import firestore
from huckleberry_api import HuckleberryAPI

TIME_KEYS = {
    "start",
    "end",
    "feedStartTime",
    "timerStartTime",
    "timerEndTime",
    "lastUpdated",
    "local_timestamp",
    "seconds",
}
DURATION_KEYS = {"duration", "leftDuration", "rightDuration"}


def _classify(value: float) -> str:
    if 1e9 <= value < 1e10:
        return "s"
    if 1e12 <= value < 1e13:
        return "ms"
    if value == 0:
        return "zero"
    return "?"


def _fmt_time(value: object, tz: ZoneInfo) -> str:
    """Render a numeric or native timestamp with its apparent unit."""
    if isinstance(value, datetime):
        local = value.astimezone(tz)
        return f"{local:%Y-%m-%d %H:%M:%S} (native Firestore Timestamp)"
    if not isinstance(value, (int, float)):
        return repr(value)
    unit = _classify(float(value))
    if unit == "s":
        secs = float(value)
    elif unit == "ms":
        secs = float(value) / 1000
    else:
        return f"{value!r}  <-- UNRECOGNISED UNIT ({unit})"
    try:
        when = datetime.fromtimestamp(secs, tz)
    except (OverflowError, OSError, ValueError):
        return f"{value!r}  <-- out of range"
    age = datetime.now(tz) - when
    hours = age.total_seconds() / 3600
    flag = ""
    if unit == "ms":
        flag = "  <-- MILLISECONDS"
    if hours < 0:
        flag += "  <-- IN THE FUTURE"
    return f"{value!r} [{unit}] = {when:%Y-%m-%d %H:%M:%S} ({hours:.1f}h ago){flag}"


def _fmt_duration(value: object) -> str:
    if not isinstance(value, (int, float)):
        return repr(value)
    secs = float(value)
    flag = ""
    if secs > 12 * 3600:
        flag = "  <-- LONGER THAN 12H (milliseconds, or a stuck timer?)"
    if secs < 0:
        flag = "  <-- NEGATIVE"
    return f"{value!r} = {secs / 60:.1f} min{flag}"


def _dump(obj: object, tz: ZoneInfo, indent: int = 2) -> None:
    pad = " " * indent
    if isinstance(obj, dict):
        for key, val in obj.items():
            if isinstance(val, (dict, list)):
                print(f"{pad}{key}:")
                _dump(val, tz, indent + 2)
            elif key in TIME_KEYS:
                print(f"{pad}{key}: {_fmt_time(val, tz)}")
            elif key in DURATION_KEYS:
                print(f"{pad}{key}: {_fmt_duration(val)}")
            elif isinstance(val, datetime):
                print(f"{pad}{key}: {_fmt_time(val, tz)}")
            else:
                print(f"{pad}{key}: {val!r}")
    elif isinstance(obj, list):
        for i, val in enumerate(obj):
            print(f"{pad}[{i}]")
            _dump(val, tz, indent + 2)
    else:
        print(f"{pad}{obj!r}")


def _section(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def _id_time(doc_id: str, tz: ZoneInfo) -> str:
    head = doc_id.split("-", 1)[0]
    if head.isdigit():
        return _fmt_time(int(head), tz)
    return "(id is not timestamp-prefixed)"


async def main() -> int:
    parser = argparse.ArgumentParser(prog="huckdeck.diag_feed")
    parser.add_argument("-n", type=int, default=25, help="how many recent feed intervals to list")
    parser.add_argument("--raw", action="store_true", help="also print the raw documents")
    parser.add_argument("--child", default="", help="child cid (defaults to config.yaml / first child)")
    args = parser.parse_args()

    load_dotenv()
    email = os.environ.get("HUCKLEBERRY_EMAIL")
    password = os.environ.get("HUCKLEBERRY_PASSWORD")
    tz_name = os.environ.get("HUCKLEBERRY_TZ", "America/New_York")
    if not email or not password:
        print("Set HUCKLEBERRY_EMAIL and HUCKLEBERRY_PASSWORD in .env (see .env.example)")
        return 1
    tz = ZoneInfo(tz_name)

    pinned = args.child
    if not pinned:
        for candidate in (Path.cwd() / "config.yaml", Path(__file__).resolve().parents[2] / "config.yaml"):
            if candidate.is_file():
                pinned = (yaml.safe_load(candidate.read_text()).get("child_uid") or "").strip()
                break

    async with aiohttp.ClientSession() as websession:
        api = HuckleberryAPI(email=email, password=password, timezone=tz_name, websession=websession)
        await api.authenticate()
        user = await api.get_user()
        if user is None or not user.childList:
            print("No children on this account")
            return 1
        child = next((c for c in user.childList if c.cid == pinned), None) if pinned else None
        if child is None:
            child = user.childList[0]
        print(f"Child: {child.nickname or '?'} ({child.cid})")
        print(f"Now:   {datetime.now(tz):%Y-%m-%d %H:%M:%S} {tz_name}")
        print(f"Unix:  {datetime.now(timezone.utc).timestamp():.0f} s")

        client = await api._get_firestore_client()  # noqa: SLF001 - diagnostic tool
        feed_ref = client.collection("feed").document(child.cid)

        feed_doc = await feed_ref.get(timeout=10.0)
        feed = feed_doc.to_dict() or {}

        _section("feed/{child}.timer   (live nursing timer)")
        timer = feed.get("timer")
        if timer is None:
            print("  (no timer field)")
        else:
            _dump(timer, tz)
            fst, tst = timer.get("feedStartTime"), timer.get("timerStartTime")
            if isinstance(fst, (int, float)) and isinstance(tst, (int, float)):
                if _classify(fst) != _classify(tst):
                    print("  !! feedStartTime and timerStartTime are in DIFFERENT units")

        _section("feed/{child}.prefs   (what 'Last fed' / Live Activity reads)")
        prefs = feed.get("prefs") or {}
        for key in ("lastNursing", "lastNursingUuid", "lastSide", "lastBottle", "lastBottleUuid", "timestamp", "local_timestamp"):
            if key in prefs:
                val = prefs[key]
                if isinstance(val, dict):
                    print(f"  {key}:")
                    _dump(val, tz, 4)
                elif key in TIME_KEYS:
                    print(f"  {key}: {_fmt_time(val, tz)}")
                else:
                    print(f"  {key}: {val!r}")
        other = sorted(set(prefs) - {"lastNursing", "lastNursingUuid", "lastSide", "lastBottle", "lastBottleUuid", "timestamp", "local_timestamp"})
        if other:
            print(f"  (other prefs keys: {', '.join(other)})")

        _section(f"feed/{{child}}/intervals   (newest {args.n} by document id)")
        intervals_ref = feed_ref.collection("intervals")
        query = intervals_ref.order_by("__name__", direction=firestore.Query.DESCENDING).limit(args.n)
        rows = [doc async for doc in query.stream()]
        if not rows:
            print("  (none)")
        for doc in rows:
            data = doc.to_dict() or {}
            print()
            print(f"- {doc.id}")
            print(f"    id time: {_id_time(doc.id, tz)}")
            if data.get("multi"):
                print("    (multi-entry container)")
                _dump(data, tz, 4)
                continue
            mode = data.get("mode")
            print(f"    mode: {mode!r}")
            for key in ("start", "lastSide", "leftDuration", "rightDuration", "duration", "lastUpdated", "offset", "end_offset", "amount", "units", "bottleType", "notes"):
                if key in data:
                    val = data[key]
                    if key in TIME_KEYS:
                        print(f"    {key}: {_fmt_time(val, tz)}")
                    elif key in DURATION_KEYS:
                        print(f"    {key}: {_fmt_duration(val)}")
                    else:
                        print(f"    {key}: {val!r}")
            extra = sorted(set(data) - {"mode", "start", "lastSide", "leftDuration", "rightDuration", "duration", "lastUpdated", "offset", "end_offset", "amount", "units", "bottleType", "notes"})
            if extra:
                print(f"    (other keys: {', '.join(extra)})")
            start = data.get("start")
            head = doc.id.split("-", 1)[0]
            if isinstance(start, (int, float)) and head.isdigit():
                unit = _classify(float(start))
                start_s = float(start) / 1000 if unit == "ms" else float(start)
                gap_h = (int(head) / 1000 - start_s) / 3600
                if unit == "ms":
                    print("    !! start is in MILLISECONDS; the app reads interval.start as seconds")
                if abs(gap_h) > 48:
                    print(f"    !! start is {gap_h:.0f}h away from the id timestamp")

        _section("sleep/{child}.timer   (for comparison)")
        sleep_doc = await client.collection("sleep").document(child.cid).get(timeout=10.0)
        sleep = sleep_doc.to_dict() or {}
        if sleep.get("timer") is None:
            print("  (no timer field)")
        else:
            _dump(sleep["timer"], tz)
        last_sleep = sleep.get("last")
        if last_sleep:
            print("  last:")
            _dump(last_sleep, tz, 4)

        if args.raw:
            _section("RAW feed document")
            print(repr(feed))
            _section("RAW intervals")
            for doc in rows:
                print(doc.id, repr(doc.to_dict()))
            _section("RAW sleep document")
            print(repr(sleep))

    print()
    print("Legend: [s] seconds since epoch, [ms] milliseconds. Lines marked !! or <-- need a look.")
    return 0


def run() -> None:
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    run()
