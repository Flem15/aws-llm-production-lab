import pytest


@pytest.fixture(autouse=True)
def simulated_trading_gate(monkeypatch):
    # Test brokers are in-process fakes; production defaults to disabled.
    monkeypatch.setenv("BOT_CITY_TRADING_ENABLED", "true")
