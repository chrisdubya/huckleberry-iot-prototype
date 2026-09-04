import asyncio

import pytest

from huckdeck.setup.flow import SetupFlow
from huckdeck.setup.identity import Identity
from huckdeck.setup import flow as flow_mod
from huckdeck.setup.wifi import FakeWifi


class RecordingLed:
    def __init__(self):
        self.states = []

    def set_setup(self, state):
        self.states.append(state)


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(flow_mod, "RESPONSE_FLUSH_SECONDS", 0.0)
    monkeypatch.setattr(flow_mod, "FAILED_LED_SECONDS", 0.0)
    monkeypatch.setattr(flow_mod, "CONNECTED_LED_SECONDS", 0.0)


def test_join_fails_then_succeeds():
    async def scenario():
        wifi = FakeWifi(delay=0.0)
        led = RecordingLed()
        flow = SetupFlow(wifi, led, Identity("huckdeck-test", "pw"), port=0, bind="127.0.0.1")
        task = asyncio.create_task(flow.run_wifi())
        await asyncio.sleep(0.05)
        assert flow.state.phase == "hotspot" and flow.state.hotspot_up
        assert [n.ssid for n in flow.state.networks][:2] == ["Home Wi-Fi", "Cafe Guest"]

        flow.submit("Home Wi-Fi", "wrong", False)
        await asyncio.sleep(0.05)
        assert flow.state.phase == "failed" and flow.state.hotspot_up
        assert "password" in (flow.state.error or "")

        flow.submit("Home Wi-Fi", "correct-horse", False)
        ssid = await asyncio.wait_for(task, 2)
        assert ssid == "Home Wi-Fi" and flow.state.phase == "connected" and not flow.state.hotspot_up
        assert wifi.calls == [
            "scan", "hotspot:huckdeck-test", "hotspot-down", "join:Home Wi-Fi", "scan",
            "hotspot:huckdeck-test", "hotspot-down", "join:Home Wi-Fi",
        ]
        assert led.states == ["hotspot", "joining", "failed", "hotspot", "joining", "connected"]
        await flow.close()

    asyncio.run(scenario())
