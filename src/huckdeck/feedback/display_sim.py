"""Browser simulator for the ribbon display.

Serves the live frame at http://localhost:<port>/ so layouts can be judged on
the Mac before any hardware exists. The page shows the panel at 3x in the
OLED's color and re-fetches it twice a second.
"""

from __future__ import annotations

import io

from aiohttp import web

from .display import HEIGHT, WIDTH, Display

SCALE = 3
OLED_COLOR = "#ffcf33"  # yellow panel

PAGE = f"""<!doctype html>
<title>huckdeck display</title>
<body style="margin:0;height:100vh;display:grid;place-items:center;background:#1b1b1d">
<img id="f" src="frame.png" width="{WIDTH * SCALE}" height="{HEIGHT * SCALE}"
     style="image-rendering:pixelated;border:10px solid #000;border-radius:6px">
<script>
setInterval(() => {{ document.getElementById("f").src = "frame.png?" + Date.now(); }}, 500);
</script>
"""


def to_png(frame) -> bytes:
    """Scale a display frame up and tint it like the panel."""
    from PIL import Image, ImageOps

    tinted = ImageOps.colorize(frame, black="#000000", white=OLED_COLOR)
    tinted = tinted.resize((WIDTH * SCALE, HEIGHT * SCALE), Image.Resampling.NEAREST)
    buffer = io.BytesIO()
    tinted.save(buffer, format="PNG")
    return buffer.getvalue()


async def start(display: Display, port: int) -> web.AppRunner:
    """Start serving; call cleanup() on the returned runner to stop."""

    async def page(_request: web.Request) -> web.Response:
        return web.Response(text=PAGE, content_type="text/html")

    async def frame(_request: web.Request) -> web.Response:
        return web.Response(body=to_png(display.frame()), content_type="image/png")

    app = web.Application()
    app.add_routes([web.get("/", page), web.get("/frame.png", frame)])
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "localhost", port).start()
    return runner
