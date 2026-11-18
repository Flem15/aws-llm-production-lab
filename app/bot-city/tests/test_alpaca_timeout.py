import sys
import types
from datetime import date

import pytest

from bots.brokers import AlpacaBroker
from bots.options import Contract


class Status:
    def __init__(self, value):
        self.value = value


class Order:
    def __init__(self, status="new"):
        self.id = "order-1"
        self.status = Status(status)
        self.filled_avg_price = None
        self.filled_qty = "0"


class Trading:
    def __init__(self):
        self.cancelled = False

    def submit_order(self, _request):
        return Order("new")

    def get_order_by_id(self, _order_id):
        return Order("canceled" if self.cancelled else "new")

    def cancel_order_by_id(self, _order_id):
        self.cancelled = True


def install_fake_alpaca_modules(monkeypatch):
    requests = types.ModuleType("alpaca.trading.requests")
    enums = types.ModuleType("alpaca.trading.enums")

    class MarketOrderRequest:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class TimeInForce:
        DAY = "day"

    requests.MarketOrderRequest = MarketOrderRequest
    enums.TimeInForce = TimeInForce
    monkeypatch.setitem(sys.modules, "alpaca", types.ModuleType("alpaca"))
    monkeypatch.setitem(sys.modules, "alpaca.trading", types.ModuleType("alpaca.trading"))
    monkeypatch.setitem(sys.modules, "alpaca.trading.requests", requests)
    monkeypatch.setitem(sys.modules, "alpaca.trading.enums", enums)


def test_timed_out_alpaca_order_is_cancelled(monkeypatch):
    install_fake_alpaca_modules(monkeypatch)
    broker = object.__new__(AlpacaBroker)
    broker.trading = Trading()
    broker.fill_timeout = 0
    broker.journal = types.SimpleNamespace(cancellation=lambda client_id: None)
    broker._record_order = lambda *args: None
    contract = Contract("QQQ261120C00600000", "QQQ", "C", 600, date(2026, 11, 20))
    with pytest.raises(RuntimeError, match="canceled after fill timeout"):
        broker._execute_order(contract, 1, "buy", "test-client-id")
    assert broker.trading.cancelled is True
