from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
import pytest
from test_durable import setup, begin, CONTRACT, Crash
from test_worker_risk import make_worker
from bots.durable import DurableState
from bots.ledger import DurableLedger
from bots.worker import Position
from bots.errors import SafetyHalt
from bots.analytics import build_report


def worker_session(setup, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    begin(setup)
    client, lease, broker, state = setup
    worker = make_worker()
    worker._roll_day(broker.now())
    worker.broker = broker
    worker.ledger = DurableLedger(state)
    worker.ledger.attach([worker])
    monkeypatch.setattr(broker, "pick_contract", lambda *a: CONTRACT)
    monkeypatch.setattr(broker, "option_mid", lambda *a: 2.5)
    return client, lease, broker, state, worker


def test_decision_saved_before_broker_crash(setup, tmp_path, monkeypatch):
    _, _, broker, state, worker = worker_session(setup, tmp_path, monkeypatch)
    broker.trading.crash_after_accept = True
    with pytest.raises(Crash):
        worker._enter(broker.now(), SimpleNamespace(side="CALL", reason="synthetic signal"), 600)
    assert broker.trading.submissions == 1
    intent = next(iter(state.snapshot["orders"].values()))
    assert intent["phase"] == "INTENT"
    assert intent["decision"]["strategy"] == worker.wc.strategy
    assert intent["decision"]["signal"]["reason"] == "synthetic signal"
    assert intent["decision"]["risk_controls"]["requested_qty"] > 0
    reloaded = DurableState(state.client, state.table, state.lease, state.account_id)
    reloaded.load()
    assert reloaded.snapshot["orders"] == state.snapshot["orders"]
    with pytest.raises(SafetyHalt):
        reloaded.start_session(broker)
    assert broker.trading.submissions == 1


def test_partial_fills_restart_and_durable_trade_replay(setup, tmp_path, monkeypatch):
    _, _, broker, state, worker = worker_session(setup, tmp_path, monkeypatch)
    worker.wc.dollars_per_trade = 500
    worker._enter(broker.now(), SimpleNamespace(side="CALL", reason="synthetic signal"), 600)
    saved = worker.ledger.load_positions()[worker.name]
    assert saved["journal"]["trade_id"] == worker.position.journal["trade_id"]
    # Restore as startup does; partial exits retain one close record with two IDs.
    worker.position = Position(**saved)
    original = broker.trading.submit_order
    def partial(request):
        order = original(request)
        if request.side.value == "sell" and int(request.qty) == 2:
            order.status = "canceled"; order.filled_qty = "1"
            broker.trading.positions[request.symbol] += 1
        return order
    monkeypatch.setattr(broker.trading, "submit_order", partial)
    worker.close(broker.now()+timedelta(minutes=1), "synthetic close")
    trade = state.snapshot["trades"][0]
    assert trade["reconciliation_status"] == "checkpointed"
    assert trade["strategy"] == worker.wc.strategy
    assert len(trade["executions"]["exit"]) == 2
    assert sum(o["execution"]["filled_qty"] for o in trade["executions"]["exit"]) == 2
    assert len({o["broker_id"] for o in trade["executions"]["exit"]}) == 2
    state.checkpoint({}, state.snapshot["risk"], [deepcopy(trade)])
    assert len(state.snapshot["trades"]) == 1
    changed = deepcopy(trade); changed["pnl"] = 300
    with pytest.raises(SafetyHalt):
        state.checkpoint({}, state.snapshot["risk"], [changed])
    state.finish_session(broker)
    reloaded = DurableState(state.client, state.table, state.lease, state.account_id)
    reloaded.load()
    assert build_report(reloaded.snapshot["trades"])["summary"]["trades"] == 1
    assert broker.trading.submissions == 3


def test_export_round_trip_and_repeat_daily_generation(setup, tmp_path, monkeypatch):
    import json
    from analytics_report import generate
    _, _, broker, state, worker = worker_session(setup, tmp_path, monkeypatch)
    worker._enter(broker.now(), SimpleNamespace(side="CALL", reason="test"), 600)
    worker.close(broker.now()+timedelta(minutes=1), "test")
    state.finish_session(broker)
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps({"state": state.snapshot, "revision": state.revision}, default=str))
    first = generate(path, tmp_path / "reports")
    second = generate(path, tmp_path / "reports")
    assert first["summary"] == second["summary"]
    assert first["source"]["sha256"] == second["source"]["sha256"]
    assert len(list((tmp_path / "reports/daily").glob('*.json'))) == 1


def test_partial_entry_records_actual_qty_and_net_fees(setup, tmp_path, monkeypatch):
    _, _, broker, state, worker = worker_session(setup, tmp_path, monkeypatch)
    worker.wc.dollars_per_trade = 750
    original = broker.trading.submit_order
    def partial_entry(request):
        order = original(request)
        if request.side.value == "buy":
            order.status = "canceled"; order.filled_qty = "1"
            broker.trading.positions[request.symbol] -= int(request.qty)-1
        return order
    monkeypatch.setattr(broker.trading, "submit_order", partial_entry)
    worker._enter(broker.now(), SimpleNamespace(side="CALL", reason="partial entry"), 600)
    assert worker.position.qty == 1
    assert worker.position.journal["risk_controls"]["requested_qty"] == 3
    worker.position.entry_fees = 1.25
    worker.close(broker.now()+timedelta(minutes=1), "exit")
    trade = state.snapshot["trades"][0]
    assert trade["qty"] == 1
    assert trade["pnl"] == -1.25
    assert trade["fees"] == 1.25
    assert trade["executions"]["entry"][0]["execution"]["filled_qty"] == 1


def test_pending_trade_cannot_claim_reconciliation_without_fills(setup):
    from test_analytics import row
    _, _, _, state = begin(setup)
    record = row(); record["reconciliation_status"] = "pending_checkpoint"
    with pytest.raises(SafetyHalt, match="exit executions"):
        state.checkpoint({}, {}, [record])
    assert state.snapshot["trades"] == []
