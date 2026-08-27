"""Terminal keyboard input: each mapped key acts as a deck button.

Puts the tty in cbreak mode and feeds single keypresses to the dispatcher.
The GPIO input on the Pi will implement this same run() contract.
"""

from __future__ import annotations

import asyncio
import sys
import termios
import tty

from ..dispatcher import Dispatcher

QUIT_KEYS = {"q", "\x03"}  # q or Ctrl-C


async def run(dispatcher: Dispatcher, buttons: dict[str, str]) -> None:
    """Read keys until quit. `buttons` maps key -> event name."""
    loop = asyncio.get_running_loop()
    fd = sys.stdin.fileno()
    try:
        old_settings = termios.tcgetattr(fd)
    except termios.error as exc:
        raise RuntimeError(
            "keyboard input needs an interactive terminal (or use --input gpio on the Pi)"
        ) from exc
    tty.setcbreak(fd)
    try:
        while True:
            key = await loop.run_in_executor(None, sys.stdin.read, 1)
            if key in QUIT_KEYS:
                return
            event_name = buttons.get(key)
            if event_name is not None:
                dispatcher.press(event_name)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
