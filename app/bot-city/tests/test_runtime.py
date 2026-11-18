from datetime import datetime, time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from bots.errors import SafetyHalt
from bots.runtime import session_schedule, validate_runtime_mode

ET = ZoneInfo("America/New_York")


def cfg(**overrides):
    base = dict(
        first_entry=time(9, 35),
        last_entry=time(15, 30),
        flatten=time(15, 50),
        paper=True,
        broker="alpaca",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_normal_session_keeps_configured_cutoffs():
    now = datetime(2026, 11, 10, 10, 0, tzinfo=ET)
    close = datetime(2026, 11, 10, 16, 0, tzinfo=ET)
    session = session_schedule(now, close, cfg())
    assert session.first_entry.time() == time(9, 35)
    assert session.last_entry.time() == time(15, 30)
    assert session.flatten.time() == time(15, 50)


def test_early_close_moves_flatten_and_last_entry():
    now = datetime(2026, 11, 27, 10, 0, tzinfo=ET)
    close = datetime(2026, 11, 27, 13, 0, tzinfo=ET)
    session = session_schedule(now, close, cfg())
    assert session.last_entry.time() == time(12, 30)
    assert session.flatten.time() == time(12, 50)
    assert session.market_close.time() == time(13, 0)


def test_runtime_accepts_only_alpaca_paper(monkeypatch):
    monkeypatch.setenv("BOT_CITY_MODE", "paper-alpaca")
    monkeypatch.delenv("ALLOW_LIVE_TRADING", raising=False)
    monkeypatch.delenv("WEBULL_ENDPOINT", raising=False)
    monkeypatch.delenv("WEBULL_APP_KEY", raising=False)
    monkeypatch.delenv("WEBULL_APP_SECRET", raising=False)
    assert validate_runtime_mode(cfg()) == "paper-alpaca"


@pytest.mark.parametrize(
    "changes,env_name,env_value",
    [
        ({"paper": False}, None, None),
        ({"broker": "webull"}, None, None),
        ({}, "ALLOW_LIVE_TRADING", "yes"),
        ({}, "WEBULL_ENDPOINT", "api.webull.com"),
    ],
)
def test_runtime_fails_closed(monkeypatch, changes, env_name, env_value):
    monkeypatch.setenv("BOT_CITY_MODE", "paper-alpaca")
    for name in ("ALLOW_LIVE_TRADING", "WEBULL_ENDPOINT", "WEBULL_APP_KEY", "WEBULL_APP_SECRET"):
        monkeypatch.delenv(name, raising=False)
    if env_name:
        monkeypatch.setenv(env_name, env_value)
    with pytest.raises(SafetyHalt):
        validate_runtime_mode(cfg(**changes))


@pytest.mark.parametrize("paper,live_flag", [(False, None), (False, "yes"), (True, "yes")])
def test_alpaca_client_rejects_live_at_construction(monkeypatch, paper, live_flag):
    from bots.brokers import AlpacaBroker

    monkeypatch.delenv("ALLOW_LIVE_TRADING", raising=False)
    if live_flag:
        monkeypatch.setenv("ALLOW_LIVE_TRADING", live_flag)
    with pytest.raises(SafetyHalt, match="only paper trading"):
        AlpacaBroker(paper=paper)
