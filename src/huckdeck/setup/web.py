"""aiohttp app for setup mode: the Wi-Fi form plus captive-portal redirects.

While the hotspot is up, the hotspot's DNS answers every name with the
deck's address (deploy/nm-dnsmasq-captive.conf), so phones' connectivity
probes (captive.apple.com, connectivitycheck.gstatic.com, …) land here.
Any request for a host that isn't ours gets a redirect to the setup page,
which is what makes the phone pop open its sign-in sheet.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aiohttp import web

from . import pages
from .wifi import HOTSPOT_ADDRESS

if TYPE_CHECKING:
    from .flow import SetupFlow
else:  # AppKey needs the type at runtime
    SetupFlow = object

_LOGGER = logging.getLogger(__name__)

FLOW_KEY: web.AppKey[SetupFlow] = web.AppKey("flow")
OUR_HOSTS = {HOTSPOT_ADDRESS, "huckdeck", "huckdeck.local", "localhost", "127.0.0.1"}
PSK_MIN, PSK_MAX = 8, 63


def make_app(flow: SetupFlow) -> web.Application:
    app = web.Application(middlewares=[_captive_redirect])
    app[FLOW_KEY] = flow
    app.router.add_get("/", _index)
    app.router.add_post("/wifi", _submit_wifi)
    app.router.add_get("/status.json", _status)
    app.router.add_route("*", "/{tail:.*}", _catch_all)
    return app


def _portal_url(flow: SetupFlow) -> str:
    port = "" if flow.port == 80 else f":{flow.port}"
    return f"http://{HOTSPOT_ADDRESS}{port}/"


@web.middleware
async def _captive_redirect(request: web.Request, handler):
    flow: SetupFlow = request.app[FLOW_KEY]
    host = request.host.rsplit(":", 1)[0] if request.host else ""
    # A probe uses a DNS name (captive.apple.com) that our DNS pointed here;
    # a direct visit names the address it actually connected to.
    local = request.transport.get_extra_info("sockname") if request.transport else None
    local_ip = local[0] if local else ""
    if flow.state.hotspot_up:
        # Hotspot-phase request log: shows when the phone's captive probes
        # arrive relative to the hotspot coming up (journalctl on the Pi).
        _LOGGER.info(
            "hotspot +%.1fs %s %s %s%s ua=%s",
            flow.seconds_since_hotspot_up(), request.remote, request.method, host, request.path_qs,
            request.headers.get("User-Agent", "")[:60],
        )
    if flow.state.hotspot_up and host and host not in OUR_HOSTS and host != local_ip:
        raise web.HTTPFound(_portal_url(flow))
    return await handler(request)


async def _catch_all(request: web.Request) -> web.Response:
    flow: SetupFlow = request.app[FLOW_KEY]
    if flow.state.hotspot_up:
        raise web.HTTPFound("/")
    raise web.HTTPNotFound()


def _render(flow: SetupFlow, error: str | None = None, selected: str | None = None) -> web.Response:
    state = flow.state
    if state.phase in ("hotspot", "failed"):
        html = pages.wifi_form(state.networks, error or state.error, selected)
    elif state.phase == "joining":
        html = pages.joining(state.target_ssid or "", flow.identity.ssid)
    else:
        html = pages.connected(state.connected_ssid or "", state.has_internet, state.phase == "waiting_login")
    return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-store"})


async def _index(request: web.Request) -> web.Response:
    return _render(request.app[FLOW_KEY])


async def _submit_wifi(request: web.Request) -> web.Response:
    flow: SetupFlow = request.app[FLOW_KEY]
    if flow.state.phase not in ("hotspot", "failed"):
        raise web.HTTPFound("/")
    form = await request.post()
    choice = str(form.get("network", "")).strip()
    password = str(form.get("password", ""))
    hidden = False
    if choice == "__other__":
        ssid = str(form.get("other_ssid", "")).strip()
        hidden = bool(form.get("hidden"))
        secured = True  # unknown; an empty password means open
    else:
        ssid = choice
        known = next((n for n in flow.state.networks if n.ssid == ssid), None)
        if known is None:
            return _render(flow, "Pick a network from the list.")
        secured = known.secured

    if not ssid:
        return _render(flow, "Enter the network name.", selected=choice)
    if len(ssid.encode()) > 32:
        return _render(flow, "That network name is too long.", selected=choice)
    if secured and choice != "__other__" and not password:
        return _render(flow, f"{ssid} needs a password.", selected=choice)
    if password and not (PSK_MIN <= len(password) <= PSK_MAX or (len(password) == 64 and _is_hex(password))):
        return _render(flow, "Wi‑Fi passwords are 8 to 63 characters.", selected=choice)

    flow.submit(ssid, password or None, hidden)
    return _render(flow)


def _is_hex(value: str) -> bool:
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


async def _status(request: web.Request) -> web.Response:
    state = request.app[FLOW_KEY].state
    return web.json_response(
        {
            "phase": state.phase,
            "hotspot_up": state.hotspot_up,
            "target_ssid": state.target_ssid,
            "connected_ssid": state.connected_ssid,
            "has_internet": state.has_internet,
            "error": state.error,
            "networks": [n.__dict__ for n in state.networks],
        }
    )
