import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from bots.durable import DurableState
from bots.errors import SafetyHalt
from test_durable import setup, begin, CONTRACT


def archived(setup):
    client, lease, broker, old = setup
    client.create_table(TableName="history",
        KeySchema=[{"AttributeName": "account_id", "KeyType": "HASH"},
                   {"AttributeName": "record_key", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": name, "AttributeType": "S"}
                              for name in ("account_id", "record_key")], BillingMode="PAY_PER_REQUEST")
    return DurableState(client, "trading", lease, "paper-account", "history")


def test_large_snapshot_paginated_roundtrip_and_audit(setup):
    state = archived(setup)
    state.initialize(setup[2])
    snapshot = deepcopy(state.snapshot)
    snapshot["recovery"] = {"notes": "ü" * 700_000}
    state._write(snapshot)
    assert state.load() == snapshot
    assert state.audit_history() == [1, 2]
    assert len(json.loads(setup[0].get_item(TableName="trading", Key=state.key)["Item"]["payload"]["S"]).__str__()) < 1000


def test_legacy_migration_preserves_original_revision(setup):
    begin(setup)
    old = deepcopy(setup[3].snapshot)
    revision = setup[3].revision
    state = archived(setup)
    assert state.load() == old
    state._write(old)
    assert state.audit_history() == [revision, revision + 1]
    records = list(state.archive.query("REVISION#"))
    baseline = json.loads(bytes(records[0]["payload"]["B"]))
    assert json.loads(state.archive.read(baseline)) == old


def test_missing_archive_page_blocks_load(setup):
    state = archived(setup)
    state.initialize(setup[2])
    item = next(state.archive.query("SNAPSHOT#"))
    setup[0].delete_item(TableName="history", Key={k: item[k] for k in ("account_id", "record_key")})
    with pytest.raises(SafetyHalt):
        state.load()
    assert state.failed


def test_staged_crash_leaves_authoritative_revision_unchanged(setup, monkeypatch):
    state = archived(setup)
    state.initialize(setup[2])
    original = setup[0].transact_write_items
    def fail_commit(**kwargs):
        if any(item.get("Put", {}).get("TableName") == "trading" for item in kwargs["TransactItems"]):
            raise TimeoutError("before commit")
        return original(**kwargs)
    monkeypatch.setattr(setup[0], "transact_write_items", fail_commit)
    with pytest.raises(SafetyHalt):
        state.start_session(setup[2])
    assert state.load()["status"] == "PAUSED"
    assert state.revision == 1


def test_archived_intent_unknown_commit_never_submits(setup, monkeypatch):
    state = archived(setup)
    broker = setup[2]
    state.initialize(broker)
    state.start_session(broker)
    broker.journal = state
    original = setup[0].transact_write_items
    def lost_response(**kwargs):
        result = original(**kwargs)
        if any(item.get("Put", {}).get("TableName") == "trading" for item in kwargs["TransactItems"]):
            raise TimeoutError("committed response lost")
        return result
    monkeypatch.setattr(setup[0], "transact_write_items", lost_response)
    with pytest.raises(SafetyHalt):
        broker.buy(CONTRACT, 1)
    assert broker.trading.submissions == 0
    assert len(state.load()["pending"]) == 1
    with pytest.raises(SafetyHalt):
        broker.buy(CONTRACT, 1)


def orders(count, same_time=False):
    base = datetime(2026, 11, 9, tzinfo=timezone.utc)
    return [SimpleNamespace(submitted_at=base + timedelta(seconds=0 if same_time else i),
        client_order_id=f"client-{i}", id=f"broker-{i}", symbol=CONTRACT.symbol,
        qty="1", side="buy", filled_qty="0", filled_avg_price=None,
        status="canceled", asset_class="us_option") for i in range(count)]


def serve(broker, history):
    def get(request):
        if request.status.value == "open":
            return []
        return [o for o in history if not request.after or o.submitted_at > request.after][:request.limit]
    broker.trading.get_orders = get


@pytest.mark.parametrize("count", [499, 500, 501, 1501])
def test_broker_history_all_pages(setup, count):
    broker = setup[2]
    serve(broker, orders(count))
    view = broker.reconciliation_view()
    assert len(view["orders"]) == count
    assert view["history_checkpoint"]["count"] == count


def test_same_timestamp_saturated_page_fails_closed(setup):
    broker = setup[2]
    serve(broker, orders(501, same_time=True))
    with pytest.raises(SafetyHalt, match="boundary"):
        broker.reconciliation_view()


def test_historical_order_cannot_disappear_silently(setup):
    state = archived(setup)
    broker = setup[2]
    history = orders(1001)
    serve(broker, history)
    risk = {"worker": dict(day=None, day_peak=0, off_reason=None, cooldown_until=None)}
    state.import_reviewed_state(broker, dict(positions={}, risk=risk, trades=[],
        order_workers={o.client_order_id: "worker" for o in history}))
    serve(broker, history[1:])
    with pytest.raises(SafetyHalt):
        state.start_session(broker)
    assert not state.ready


def test_default_gate_blocks_even_reconciled_owner(setup, monkeypatch):
    begin(setup)
    monkeypatch.delenv("BOT_CITY_TRADING_ENABLED", raising=False)
    with pytest.raises(SafetyHalt, match="disabled"):
        setup[2].buy(CONTRACT, 1)
    assert setup[2].trading.submissions == 0
    assert not setup[3].snapshot["pending"]


def test_missing_revision_detected_by_audit(setup):
    state = archived(setup)
    state.initialize(setup[2])
    state._write(state.snapshot)
    setup[0].delete_item(TableName="history", Key={**state.key,
        "record_key": {"S": "REVISION#00000000000000000001"}})
    with pytest.raises(SafetyHalt, match="gap"):
        state.audit_history()


def test_changed_second_broker_scan_blocks(setup):
    broker = setup[2]
    history = orders(2)
    calls = 0
    def get(request):
        nonlocal calls
        if request.status.value == "open":
            return []
        calls += 1
        return history if calls == 1 else history[1:]
    broker.trading.get_orders = get
    with pytest.raises(SafetyHalt, match="changed"):
        broker.reconciliation_view()


def test_default_main_validates_and_exits_without_strategy(setup, monkeypatch, tmp_path):
    import run_bots
    state = archived(setup)
    client, lease, broker, _ = setup
    state.initialize(broker)
    lease.release()
    from bots.lease import DynamoDbLease
    active = DynamoDbLease(client, "leases", duration=3600)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["run_bots.py", "--no-dashboard"])
    monkeypatch.delenv("BOT_CITY_TRADING_ENABLED", raising=False)
    for key, value in {"BOT_CITY_STATE_TABLE": "trading", "BOT_CITY_HISTORY_TABLE": "history"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(run_bots, "load_dotenv", lambda: None)
    monkeypatch.setattr(run_bots, "proxy_configuration", lambda: None)
    monkeypatch.setattr(run_bots, "load_config", lambda path: SimpleNamespace(
        broker="alpaca", paper=True, fill_timeout=1, workers=[]))
    monkeypatch.setattr(run_bots, "configure_logging", lambda: None)
    monkeypatch.setattr(run_bots, "install_signal_handlers", lambda stop: None)
    monkeypatch.setattr(run_bots, "configured_lease", lambda stop: active)
    def make_broker(**kwargs):
        broker.ownership_guard = kwargs["ownership_guard"]
        return broker
    monkeypatch.setattr(run_bots, "AlpacaBroker", make_broker)
    monkeypatch.setattr(run_bots, "run_loop", lambda *a, **k: pytest.fail("strategy must not start"))
    assert run_bots.main() == 0
    assert broker.trading.submissions == 0
    assert not client.get_item(TableName="leases", Key=active.key).get("Item")
    manifest = json.loads(client.get_item(TableName="trading", Key=state.key)["Item"]["payload"]["S"])
    assert json.loads(state.archive.read(manifest))["status"] == "CLEAN"
