from datetime import date

from bots.brokers import WebullBroker
from bots.options import Contract


def test_webull_option_payload_contains_current_required_order_fields():
    broker = object.__new__(WebullBroker)
    contract = Contract("QQQ261120C00600000", "QQQ", "C", 600, date(2026, 11, 20))
    payload = broker._order_payload(contract, 1, "BUY", 2.50, "test-order-id")[0]
    assert payload["instrument_type"] == "OPTION"
    assert payload["market"] == "US"
    assert payload["symbol"] == "QQQ"
    assert payload["option_strategy"] == "SINGLE"
    assert payload["position_intent"] == "BUY_TO_OPEN"
