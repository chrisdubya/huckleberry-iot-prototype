"""Wi-Fi control for setup mode.

`NmcliWifi` drives NetworkManager (what Raspberry Pi OS ships) through the
`nmcli` CLI — no D-Bus bindings to build on the Pi. `FakeWifi` is an
in-memory stand-in so the web flow can be exercised on a Mac:

    uv run huckdeck --setup --wifi fake --setup-port 8080

The Pi Zero 2 W has one radio, so the hotspot and the home network can't
be up at the same time; the flow stops the hotspot before joining.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)

HOTSPOT_CONNECTION = "huckdeck-setup"
HOTSPOT_ADDRESS = "10.42.0.1"
WIFI_INTERFACE = "wlan0"
JOIN_TIMEOUT_SECONDS = 60


class WifiError(Exception):
    """A NetworkManager operation failed; the message is user-presentable."""


@dataclass(frozen=True)
class Network:
    ssid: str
    signal: int  # 0-100
    secured: bool


class Wifi:
    """Interface both backends implement."""

    async def scan(self) -> list[Network]:
        raise NotImplementedError

    async def has_saved_network(self) -> bool:
        raise NotImplementedError

    async def start_hotspot(self, ssid: str, password: str) -> None:
        raise NotImplementedError

    async def stop_hotspot(self) -> None:
        raise NotImplementedError

    async def join(self, ssid: str, password: str | None, hidden: bool = False) -> None:
        """Save and connect to a network. Raises WifiError and leaves nothing saved on failure."""
        raise NotImplementedError

    async def current_ssid(self) -> str | None:
        raise NotImplementedError

    async def has_internet(self) -> bool:
        raise NotImplementedError


# --- nmcli ------------------------------------------------------------------


def split_terse(line: str) -> list[str]:
    """Split one line of `nmcli -t -e yes` output on unescaped colons."""
    fields: list[str] = []
    current: list[str] = []
    escaped = False
    for ch in line:
        if escaped:
            current.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(ch)
    fields.append("".join(current))
    return fields


def parse_scan(output: str) -> list[Network]:
    """Parse `nmcli -t -e yes -f SSID,SIGNAL,SECURITY dev wifi list`.

    Hidden networks (empty SSID) are dropped; an SSID seen on several
    access points keeps its strongest signal. Sorted strongest first.
    """
    best: dict[str, Network] = {}
    for line in output.splitlines():
        if not line.strip():
            continue
        fields = split_terse(line)
        if len(fields) < 3:
            continue
        ssid, signal_str, security = fields[0], fields[1], ":".join(fields[2:])
        if not ssid:
            continue
        try:
            signal = int(signal_str)
        except ValueError:
            signal = 0
        network = Network(ssid=ssid, signal=signal, secured=bool(security.strip()))
        if ssid not in best or best[ssid].signal < signal:
            best[ssid] = network
    return sorted(best.values(), key=lambda n: (-n.signal, n.ssid.lower()))


class NmcliWifi(Wifi):
    def __init__(self, interface: str = WIFI_INTERFACE) -> None:
        self._interface = interface

    async def _run(self, *args: str, check: bool = True) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                "nmcli", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
        except FileNotFoundError as exc:
            raise WifiError("nmcli not found — is NetworkManager installed? (use --wifi fake off the Pi)") from exc
        stdout, stderr = await proc.communicate()
        out = stdout.decode(errors="replace")
        err = stderr.decode(errors="replace").strip()
        if proc.returncode != 0 and check:
            _LOGGER.warning("nmcli %s failed (%s): %s", " ".join(args[:3]), proc.returncode, err)
            raise WifiError(err or f"nmcli exited {proc.returncode}")
        return out

    async def scan(self) -> list[Network]:
        out = await self._run(
            "-t", "-e", "yes", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list",
            "ifname", self._interface, "--rescan", "yes",
        )
        return parse_scan(out)

    async def _saved_wifi_connections(self) -> list[str]:
        out = await self._run("-t", "-e", "yes", "-f", "NAME,TYPE", "con", "show")
        names = []
        for line in out.splitlines():
            fields = split_terse(line)
            if len(fields) >= 2 and fields[1] == "802-11-wireless" and fields[0] != HOTSPOT_CONNECTION:
                names.append(fields[0])
        return names

    async def has_saved_network(self) -> bool:
        return bool(await self._saved_wifi_connections())

    async def start_hotspot(self, ssid: str, password: str) -> None:
        await self._run("con", "delete", HOTSPOT_CONNECTION, check=False)
        await self._run(
            "con", "add", "type", "wifi", "ifname", self._interface,
            "con-name", HOTSPOT_CONNECTION, "autoconnect", "no", "ssid", ssid,
            "802-11-wireless.mode", "ap", "802-11-wireless.band", "bg",
            "ipv4.method", "shared", "ipv4.addresses", f"{HOTSPOT_ADDRESS}/24",
            "ipv6.method", "disabled",
            "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.proto", "rsn",
            "wifi-sec.pairwise", "ccmp", "wifi-sec.group", "ccmp",
            "wifi-sec.psk", password,
        )
        await self._run("con", "up", HOTSPOT_CONNECTION)
        _LOGGER.info("Hotspot %s up at %s", ssid, HOTSPOT_ADDRESS)

    async def stop_hotspot(self) -> None:
        await self._run("con", "down", HOTSPOT_CONNECTION, check=False)
        await self._run("con", "delete", HOTSPOT_CONNECTION, check=False)

    async def join(self, ssid: str, password: str | None, hidden: bool = False) -> None:
        # One saved profile per SSID: replace any earlier attempt.
        await self._run("con", "delete", ssid, check=False)
        args = [
            "con", "add", "type", "wifi", "ifname", self._interface,
            "con-name", ssid, "ssid", ssid, "connection.autoconnect", "yes",
        ]
        if hidden:
            args += ["802-11-wireless.hidden", "yes"]
        if password:
            args += ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password]
        await self._run(*args)
        try:
            await self._run("--wait", str(JOIN_TIMEOUT_SECONDS), "con", "up", ssid)
        except WifiError as exc:
            await self._run("con", "delete", ssid, check=False)
            raise WifiError(_friendly_join_error(str(exc))) from exc
        _LOGGER.info("Joined %s", ssid)

    async def current_ssid(self) -> str | None:
        out = await self._run("-t", "-e", "yes", "-f", "NAME,TYPE,DEVICE", "con", "show", "--active")
        for line in out.splitlines():
            fields = split_terse(line)
            if len(fields) >= 3 and fields[1] == "802-11-wireless" and fields[0] != HOTSPOT_CONNECTION:
                return fields[0]
        return None

    async def has_internet(self) -> bool:
        out = await self._run("-t", "networking", "connectivity", "check", check=False)
        return out.strip() == "full"


def _friendly_join_error(raw: str) -> str:
    lowered = raw.lower()
    if "secrets were required" in lowered or "no secrets" in lowered:
        return "The password was rejected. Check it and try again."
    if "timeout" in lowered or "timed out" in lowered:
        return "Timed out joining the network. Is the router in range?"
    if "not found" in lowered or "no network with ssid" in lowered:
        return "Couldn't find that network. Is the name right and the router on 2.4GHz?"
    return raw or "Couldn't join the network."


# --- fake --------------------------------------------------------------------


class FakeWifi(Wifi):
    """Deterministic stand-in for development off the Pi.

    Joining any SSID containing "bad" or with a password containing "wrong" fails; every
    other join succeeds after a short delay.
    """

    def __init__(self, delay: float = 2.0) -> None:
        self._delay = delay
        self._saved: str | None = None
        self._hotspot: str | None = None
        self.calls: list[str] = []

    async def scan(self) -> list[Network]:
        self.calls.append("scan")
        await asyncio.sleep(self._delay / 4)
        return parse_scan(
            "Home Wi-Fi:88:WPA2\n"
            "Home Wi-Fi:61:WPA2\n"
            "Cafe Guest:54:\n"
            "Neighbour\\: 5G:33:WPA2 WPA3\n"
            "bad-router:20:WPA2\n"
            ":70:WPA2\n"
        )

    async def has_saved_network(self) -> bool:
        return self._saved is not None

    async def start_hotspot(self, ssid: str, password: str) -> None:
        self.calls.append(f"hotspot:{ssid}")
        self._hotspot = ssid

    async def stop_hotspot(self) -> None:
        self.calls.append("hotspot-down")
        self._hotspot = None

    async def join(self, ssid: str, password: str | None, hidden: bool = False) -> None:
        self.calls.append(f"join:{ssid}")
        await asyncio.sleep(self._delay)
        if "bad" in ssid.lower():
            raise WifiError(_friendly_join_error("Connection activation failed: timeout"))
        if password and "wrong" in password:
            raise WifiError(_friendly_join_error("Secrets were required, but not provided"))
        self._saved = ssid

    async def current_ssid(self) -> str | None:
        return self._saved

    async def has_internet(self) -> bool:
        return self._saved is not None


def make_wifi(backend: str) -> Wifi:
    if backend == "nmcli":
        return NmcliWifi()
    if backend == "fake":
        return FakeWifi()
    raise ValueError(f"unknown wifi backend {backend!r}")
