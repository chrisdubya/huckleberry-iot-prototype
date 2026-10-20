"""Epoch timestamps as Huckleberry stores them: sometimes seconds, sometimes ms.

The official iOS app writes the nursing timer's feedStartTime in milliseconds
(see py-huckleberry-api issue #51), while the library and the Android app
write seconds. Anything that reads one of these fields should go through
epoch_seconds() so a millisecond value can't land in history as year 58770.
"""

from __future__ import annotations

# Seconds-since-epoch values stay below this for ~3000 more years; millisecond
# values passed it in 1973. Anything above is treated as milliseconds.
_MS_THRESHOLD = 1e11


def is_millis(value: float | int) -> bool:
    return float(value) > _MS_THRESHOLD


def epoch_seconds(value: float | int) -> float:
    """Return `value` in seconds whether it was stored as seconds or milliseconds."""
    value = float(value)
    return value / 1000 if value > _MS_THRESHOLD else value
