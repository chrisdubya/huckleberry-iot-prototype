import asyncio
import json

import pytest

from huckdeck import credentials
from huckdeck.client import AuthError, Child
from huckdeck.setup.flow import SetupFlow
from huckdeck.setup.identity import Identity
from huckdeck.setup.wifi import FakeWifi


class RecordingLed:
    def __init__(self):
        self.states = []

    def set_setup(self, state):
        self.states.append(state)


async def fake_sign_in(email, password, timezone, session):
    if password != "hunter22":
        raise AuthError("Email or password not recognised.")
    if email.startswith("one"):
        return "tok-1", [Child("c1", "Only")]
    return "tok-2", [Child("c1", "Ada"), Child("c2", "Ben")]


@pytest.fixture(autouse=True)
def no_dotenv(monkeypatch):
    # python-dotenv searches up from the package dir, which would find the repo's real .env
    monkeypatch.setattr(credentials, "load_dotenv", lambda *a, **k: False)
    monkeypatch.delenv("HUCKLEBERRY_EMAIL", raising=False)
    monkeypatch.delenv("HUCKLEBERRY_PASSWORD", raising=False)


def test_credentials_roundtrip_never_stores_password(tmp_path):
    path = tmp_path / "creds.json"
    assert credentials.load(path) is None
    creds = credentials.Credentials(email="a@b.c", timezone="Europe/London", password="secret",
                                    refresh_token="tok", child_uid="c1", child_name="Ada")
    credentials.save(creds, path)
    assert "secret" not in path.read_text()
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    loaded = credentials.load(path)
    assert loaded == credentials.Credentials(email="a@b.c", timezone="Europe/London", refresh_token="tok",
                                             child_uid="c1", child_name="Ada")
    assert loaded.source == "file"
    credentials.clear(path)
    assert credentials.load(path) is None


def test_env_wins_over_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HUCKLEBERRY_EMAIL", "env@x.y")
    monkeypatch.setenv("HUCKLEBERRY_PASSWORD", "pw")
    monkeypatch.setenv("HUCKLEBERRY_TZ", "Asia/Tokyo")
    path = tmp_path / "creds.json"
    path.write_text(json.dumps({"email": "file@x.y", "refresh_token": "t"}))
    creds = credentials.load(path)
    assert creds.source == "env" and creds.email == "env@x.y" and creds.timezone == "Asia/Tokyo"


def _flow(tmp_path):
    return SetupFlow(FakeWifi(delay=0), RecordingLed(), Identity("huckdeck-t", "pw"), port=0,
                     bind="127.0.0.1", sign_in_fn=fake_sign_in, credentials_path=tmp_path / "creds.json")


def test_login_with_child_picker(tmp_path):
    async def scenario():
        flow = _flow(tmp_path)
        task = asyncio.create_task(flow.wait_for_login())
        await asyncio.sleep(0.02)
        assert flow.state.phase == "login" and flow.led.states == ["waiting_login"]

        assert await flow.try_sign_in("p@x.y", "nope", "Europe/Paris") == "Email or password not recognised."
        assert flow.state.phase == "login" and flow.state.email == "p@x.y"

        assert await flow.try_sign_in("p@x.y", "hunter22", "Europe/Paris") is None
        assert flow.state.phase == "choose_child" and [c.name for c in flow.state.children] == ["Ada", "Ben"]
        assert flow.choose_child("zzz") == "Pick a child from the list."
        assert flow.choose_child("c2") is None

        creds = await asyncio.wait_for(task, 1)
        assert creds.refresh_token == "tok-2" and creds.child_uid == "c2" and creds.child_name == "Ben"
        assert creds.timezone == "Europe/Paris" and creds.password is None
        assert credentials.load(tmp_path / "creds.json") == creds
        assert flow.state.phase == "starting" and flow.state.children == []
        await flow.close()

    asyncio.run(scenario())


def test_login_single_child_skips_picker(tmp_path):
    async def scenario():
        flow = _flow(tmp_path)
        task = asyncio.create_task(flow.wait_for_login())
        await asyncio.sleep(0.02)
        assert await flow.try_sign_in("one@x.y", "hunter22", "UTC") is None
        creds = await asyncio.wait_for(task, 1)
        assert creds.child_name == "Only" and creds.refresh_token == "tok-1"
        await flow.close()

    asyncio.run(scenario())
