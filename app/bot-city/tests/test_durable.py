from copy import deepcopy
from datetime import datetime, date, timezone
from types import SimpleNamespace

import boto3
from moto import mock_aws
import pytest

from bots.brokers import AlpacaBroker
from bots.durable import DurableState, configured_state
from bots.errors import SafetyHalt
from bots.lease import DynamoDbLease
from bots.ledger import DurableLedger
from bots.options import Contract
from bots.worker import Position

CONTRACT = Contract("QQQ261120C00600000", "QQQ", "C", 600, date(2026, 11, 20))


class Crash(BaseException):
    pass


class Paper:
    def __init__(self):
        self.orders = {}
        self.positions = {}
        self.submissions = 0
        self.crash_after_accept = False
        self.fail_poll = False

    def get_account(self):
        return SimpleNamespace(id="paper-account")

    def get_all_positions(self):
        return [SimpleNamespace(symbol=symbol, qty=str(qty), side="long", asset_class="us_option")
                for symbol, qty in self.positions.items() if qty]

    def get_orders(self, request):
        return [] if request.status.value == "open" else list(self.orders.values())

    def submit_order(self, request):
        self.submissions += 1
        order = SimpleNamespace(
            submitted_at=datetime(2026, 11, 10, 11, tzinfo=timezone.utc),
            client_order_id=request.client_order_id, id=f"broker-{self.submissions}",
            symbol=request.symbol, qty=str(request.qty), side=request.side,
            filled_qty=str(request.qty), filled_avg_price="2.50", status="filled", asset_class="us_option")
        self.orders[request.client_order_id] = order
        sign = 1 if request.side.value == "buy" else -1
        self.positions[request.symbol] = self.positions.get(request.symbol, 0) + sign * int(request.qty)
        if self.crash_after_accept:
            raise Crash()
        return order

    def get_order_by_id(self, order_id):
        if self.fail_poll:
            raise TimeoutError("broker unavailable")
        return next(order for order in self.orders.values() if order.id == order_id)


@pytest.fixture
def setup():
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-2",
                             aws_access_key_id="testing", aws_secret_access_key="testing")
        for table, key in (("leases", "lease_key"), ("trading", "account_id")):
            client.create_table(TableName=table,
                KeySchema=[{"AttributeName": key, "KeyType": "HASH"}],
                AttributeDefinitions=[{"AttributeName": key, "AttributeType": "S"}],
                BillingMode="PAY_PER_REQUEST")
        lease = DynamoDbLease(client, "leases", duration=3600)
        lease.acquire()
        broker = object.__new__(AlpacaBroker)
        broker.trading = Paper()
        broker.ownership_guard = lease.assert_owned
        broker.journal = None
        broker.worker_name = "worker"
        broker.fill_timeout = 1
        broker.now = lambda: datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
        durable = DurableState(client, "trading", lease, "paper-account")
        yield client, lease, broker, durable


def begin(setup):
    client, lease, broker, state = setup
    state.initialize(broker)
    state.start_session(broker)
    broker.journal = state
    return client, lease, broker, state


def replacement(setup):
    client, lease, broker, old = setup
    # Simulates explicitly reviewed operator recovery AFTER the old process has exited.
    client.delete_item(TableName="leases", Key=lease.key)
    new_lease = DynamoDbLease(client, "leases", duration=3600)
    new_lease.acquire()
    broker.ownership_guard = new_lease.assert_owned
    state = DurableState(client, "trading", new_lease, "paper-account")
    broker.journal = state
    return client, new_lease, broker, state


def reviewed_position():
    return {"worker": {
        "contract": {"symbol": CONTRACT.symbol, "underlying": "QQQ", "kind": "C",
                     "strike": 600, "expiry": "2026-11-20"},
        "qty": 1, "entry_price": 2.5, "entry_time": "2026-11-10T12:00:00+00:00",
        "entry_fees": 0, "peak": 2.5, "reason": "operator reviewed broker fill"}}


def test_startup_requires_initialized_state_and_reconciliation(setup):
    _, _, broker, state = setup
    broker.journal = state
    with pytest.raises(SafetyHalt, match="initialized"):
        state.start_session(broker)
    with pytest.raises(SafetyHalt):
        broker.buy(CONTRACT, 1)
    assert broker.trading.submissions == 0


def test_crash_before_submit_never_replays_intent(setup):
    begin(setup)
    _, _, broker, state = setup
    client_id = state.begin_order(CONTRACT, 1, "buy", "worker")
    _, _, broker, restarted = replacement(setup)
    with pytest.raises(SafetyHalt):
        restarted.start_session(broker)
    with pytest.raises(SafetyHalt):
        broker.buy(CONTRACT, 1)
    assert broker.trading.submissions == 0
    restarted.recover(broker, dict(positions={}, risk={}, trades=[],
                                  absent_intents=[client_id]), restarted.revision)
    restarted.start_session(broker)
    assert restarted.ready
    assert broker.trading.submissions == 0


def test_crash_after_broker_acceptance_blocks_duplicate_until_review(setup):
    begin(setup)
    _, _, broker, state = setup
    broker.trading.crash_after_accept = True
    with pytest.raises(Crash):
        broker.buy(CONTRACT, 1)
    assert broker.trading.submissions == 1
    _, _, broker, restarted = replacement(setup)
    with pytest.raises(SafetyHalt):
        restarted.start_session(broker)
    with pytest.raises(SafetyHalt):
        broker.buy(CONTRACT, 1)
    assert broker.trading.submissions == 1
    restarted.recover(broker, dict(positions=reviewed_position(),
        risk={"worker": {"day": "2026-11-10", "day_peak": 0,
                         "off_reason": None, "cooldown_until": None}},
        trades=[], absent_intents=[]), restarted.revision)
    restarted.start_session(broker)
    assert restarted.ready
    assert broker.trading.submissions == 1
    record = next(iter(restarted.snapshot["orders"].values()))
    assert record["broker_id"] == "broker-1"
    assert record["execution"]["filled_qty"] == 1


def test_terminal_fill_and_positions_commit_atomically(setup, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    begin(setup)
    _, _, broker, state = setup
    broker.buy(CONTRACT, 1)
    assert state.snapshot["pending"]
    ledger = DurableLedger(state)
    worker = SimpleNamespace(name="worker",
        position=Position(CONTRACT, 1, 2.5, broker.now(), 0, 2.5, "test"),
        day=broker.now().date(), day_peak=50, off_reason=None, cooldown_until=None)
    ledger.attach([worker])
    ledger.save_positions()
    # A position-only write cannot erase the unresolved operation.
    assert state.snapshot["pending"]
    ledger.save_risk_state()
    reread = DurableState(state.client, state.table, state.lease, state.account_id)
    reread.load()
    assert not reread.snapshot["pending"]
    assert reread.snapshot["positions"]["worker"]["qty"] == 1
    assert reread.snapshot["risk"]["worker"]["day_peak"] == 50
    assert next(iter(reread.snapshot["orders"].values()))["phase"] == "APPLIED"


def test_lease_loss_prevents_durable_intent_and_submission(setup):
    begin(setup)
    client, lease, broker, state = setup
    client.put_item(TableName="leases", Item={**lease.key, "owner": {"S": "other"}})
    with pytest.raises(SafetyHalt):
        broker.buy(CONTRACT, 1)
    assert broker.trading.submissions == 0


def test_conflicting_snapshot_revision_fails_closed(setup):
    begin(setup)
    client, lease, broker, state = setup
    stale = DurableState(client, "trading", lease, "paper-account")
    stale.load()
    stale.ready = True
    state.checkpoint({}, {}, [])
    with pytest.raises(SafetyHalt):
        stale.begin_order(CONTRACT, 1, "buy", "worker")
    assert stale.failed
    assert broker.trading.submissions == 0


def test_commit_succeeded_but_response_lost_cannot_retry(setup, monkeypatch):
    begin(setup)
    client, _, broker, state = setup
    original = client.transact_write_items
    def ambiguous(**kwargs):
        original(**kwargs)
        raise TimeoutError("response lost")
    monkeypatch.setattr(client, "transact_write_items", ambiguous)
    with pytest.raises(SafetyHalt):
        broker.buy(CONTRACT, 1)
    assert broker.trading.submissions == 0
    with pytest.raises(SafetyHalt):
        broker.buy(CONTRACT, 1)
    monkeypatch.setattr(client, "transact_write_items", original)
    _, _, broker, restarted = replacement(setup)
    with pytest.raises(SafetyHalt):
        restarted.start_session(broker)
    assert len(restarted.snapshot["pending"]) == 1


def test_read_failure_after_submit_latches_recovery(setup):
    begin(setup)
    _, _, broker, state = setup
    broker.trading.fail_poll = True
    with pytest.raises(SafetyHalt):
        broker.buy(CONTRACT, 1)
    with pytest.raises(SafetyHalt):
        broker.buy(CONTRACT, 1)
    assert broker.trading.submissions == 1
    assert state.snapshot["status"] == "RECOVERY_REQUIRED"


def test_unclean_flat_restart_requires_operator_acknowledgment(setup):
    begin(setup)
    _, _, broker, restarted = replacement(setup)
    with pytest.raises(SafetyHalt, match="unclean"):
        restarted.start_session(broker)
    restarted.recover(broker, dict(positions={}, risk={}, trades=[], absent_intents=[]),
                      restarted.revision)
    restarted.start_session(broker)
    assert restarted.ready


def test_clean_shutdown_can_restart_after_reconciliation(setup):
    begin(setup)
    client, lease, broker, state = setup
    state.finish_session(broker)
    lease.release()
    new_lease = DynamoDbLease(client, "leases", duration=3600)
    new_lease.acquire()
    restarted = DurableState(client, "trading", new_lease, "paper-account")
    restarted.start_session(broker)
    assert restarted.ready


def test_broker_discrepancy_blocks_clean_restart(setup):
    begin(setup)
    _, _, broker, state = setup
    state.finish_session(broker)
    broker.trading.positions[CONTRACT.symbol] = 1
    with pytest.raises(SafetyHalt, match="positions differ"):
        state.start_session(broker)
    assert not state.ready


def test_stale_operator_review_cannot_overwrite_new_state(setup):
    begin(setup)
    _, _, broker, state = setup
    revision = state.revision
    state.checkpoint({}, {}, [])
    with pytest.raises(SafetyHalt, match="stale"):
        state.recover(broker, dict(positions={}, risk={}, trades=[], absent_intents=[]), revision)


def test_cloud_runtime_has_no_local_state_fallback(setup, monkeypatch):
    _, lease, broker, _ = setup
    monkeypatch.delenv("BOT_CITY_STATE_TABLE", raising=False)
    with pytest.raises(SafetyHalt):
        configured_state(lease, broker)
    monkeypatch.setenv("BOT_CITY_STATE_TABLE", "trading")
    monkeypatch.setenv("BOT_CITY_HISTORY_TABLE", "history")
    with pytest.raises(SafetyHalt):
        configured_state(None, broker)
    monkeypatch.setenv("BOT_CITY_STATE_TABLE", "leases")
    with pytest.raises(SafetyHalt):
        configured_state(lease, broker)


def test_malformed_risk_snapshot_is_rejected(setup):
    begin(setup)
    _, _, broker, state = setup
    with pytest.raises(SafetyHalt, match="invalid"):
        state.checkpoint({}, {"worker": {"day": "2026-11-10", "day_peak": float("nan"),
                                        "off_reason": None, "cooldown_until": None}}, [])
    assert broker.trading.submissions == 0


def test_broker_history_truncation_blocks_trading(setup):
    _, _, broker, state = setup
    broker.trading.get_orders = lambda request: [] if request.status.value == "open" else [object()] * 500
    with pytest.raises(SafetyHalt, match="reconciliation"):
        state.initialize(broker)


def test_open_broker_order_blocks_initialization_without_cancel(setup):
    _, _, broker, state = setup
    broker.trading.get_orders = lambda request: [object()]
    with pytest.raises(SafetyHalt, match="open broker"):
        state.initialize(broker)
    assert broker.trading.submissions == 0


def test_runtime_entrypoint_cannot_resume_after_accepted_order_crash(setup, monkeypatch, tmp_path):
    import run_bots
    _, lease, broker, state = setup
    from test_archive import archived
    state = archived(setup)
    state.initialize(broker)
    lease.release()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["run_bots.py", "--no-dashboard"])
    monkeypatch.setenv("BOT_CITY_STATE_TABLE", "trading")
    monkeypatch.setenv("BOT_CITY_HISTORY_TABLE", "history")
    monkeypatch.setattr(run_bots, "load_dotenv", lambda: None)
    monkeypatch.setattr(run_bots, "proxy_configuration", lambda: None)
    monkeypatch.setattr(run_bots, "load_config", lambda path: SimpleNamespace(
        broker="alpaca", paper=True, fill_timeout=1, workers=[]))
    monkeypatch.setattr(run_bots, "configure_logging", lambda: None)
    monkeypatch.setattr(run_bots, "validate_runtime_mode", lambda cfg: "paper-alpaca")
    monkeypatch.setattr(run_bots, "install_signal_handlers", lambda stop: None)
    leases = []
    def configure(stop):
        active = DynamoDbLease(state.client, "leases", duration=3600, on_loss=stop.set)
        leases.append(active)
        return active
    monkeypatch.setattr(run_bots, "configured_lease", configure)
    def make_broker(**kwargs):
        broker.ownership_guard = kwargs["ownership_guard"]
        return broker
    monkeypatch.setattr(run_bots, "AlpacaBroker", make_broker)
    def trade(*args, **kwargs):
        broker.trading.crash_after_accept = True
        broker.buy(CONTRACT, 1)
    monkeypatch.setattr(run_bots, "run_loop", trade)
    with pytest.raises(Crash):
        run_bots.main()
    assert broker.trading.submissions == 1
    # Even after an operator clears the dead process's sticky lease, journal state blocks restart.
    state.client.delete_item(TableName="leases", Key=lease.key)
    assert run_bots.main() == 2
    assert broker.trading.submissions == 1


def test_full_buy_sell_cycle_commits_trade_once(setup, tmp_path, monkeypatch):
    from test_worker_risk import make_worker
    monkeypatch.chdir(tmp_path)
    begin(setup)
    _, _, broker, state = setup
    worker = make_worker()
    broker.worker_name = worker.name
    broker.buy(CONTRACT, 1)
    ledger = DurableLedger(state)
    worker.broker, worker.ledger = broker, ledger
    worker._roll_day(broker.now())
    worker.position = Position(CONTRACT, 1, 2.5, broker.now(), 0, 2.5, "entry")
    ledger.attach([worker])
    ledger.save_risk_state()
    worker.close(broker.now(), "test exit")
    assert not state.snapshot["positions"]
    assert len(state.snapshot["trades"]) == 1
    assert not state.snapshot["pending"]
    state.finish_session(broker)
    state.start_session(broker)
    assert ledger.todays_trades(broker.now().date()) == [(worker.name, 0.0)]
    assert broker.trading.submissions == 2


def test_explicit_migration_preserves_reviewed_legacy_state(setup):
    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce
    _, _, broker, state = setup
    order = broker.trading.submit_order(MarketOrderRequest(
        symbol=CONTRACT.symbol, qty=1, side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY, client_order_id="legacy-order"))
    risk = {"worker": {"day": "2026-11-10", "day_peak": 800,
                       "off_reason": "profit lock", "cooldown_until": None}}
    state.import_reviewed_state(broker, dict(positions=reviewed_position(), risk=risk,
        trades=[], order_workers={order.client_order_id: "worker"}))
    state.start_session(broker)
    assert state.snapshot["risk"]["worker"]["day_peak"] == 800
    assert state.snapshot["risk"]["worker"]["off_reason"] == "profit lock"
    assert broker.trading.submissions == 1


def test_partial_exit_records_each_execution_before_position_checkpoint(setup, tmp_path, monkeypatch):
    from test_worker_risk import make_worker
    monkeypatch.chdir(tmp_path)
    begin(setup)
    _, _, broker, state = setup
    worker = make_worker()
    worker._roll_day(broker.now())
    broker.worker_name = worker.name
    broker.buy(CONTRACT, 2)
    ledger = DurableLedger(state)
    worker.broker, worker.ledger = broker, ledger
    worker.position = Position(CONTRACT, 2, 2.5, broker.now(), 0, 2.5, "entry")
    ledger.attach([worker])
    ledger.save_risk_state()
    original = broker.trading.submit_order
    def partial(request):
        order = original(request)
        if request.side.value == "sell" and int(request.qty) == 2:
            order.status = "canceled"
            order.filled_qty = "1"
            broker.trading.positions[request.symbol] += 1
        return order
    monkeypatch.setattr(broker.trading, "submit_order", partial)
    worker.close(broker.now(), "partial exit test")
    assert broker.trading.submissions == 3
    assert not state.snapshot["positions"]
    assert len(state.snapshot["trades"]) == 1
    executions = [r["execution"] for r in state.snapshot["orders"].values() if r["side"] == "sell"]
    assert sum(execution["filled_qty"] for execution in executions) == 2


def test_reconciliation_detects_changed_fill_details(setup, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    begin(setup)
    _, _, broker, state = setup
    broker.buy(CONTRACT, 1)
    risk = {"worker": {"day": "2026-11-10", "day_peak": 0,
                       "off_reason": None, "cooldown_until": None}}
    state.checkpoint(reviewed_position(), risk, [])
    order = next(iter(broker.trading.orders.values()))
    order.filled_avg_price = "2.75"
    with pytest.raises(SafetyHalt, match="execution/fill"):
        state._verify(broker, state.snapshot)


def test_snapshot_capacity_blocks_order_before_submission(setup):
    begin(setup)
    _, _, broker, state = setup
    state.snapshot["recovery"] = {"notes": "x" * 300_000}
    with pytest.raises(SafetyHalt, match="capacity"):
        broker.buy(CONTRACT, 1)
    assert broker.trading.submissions == 0
    assert not state.ready


def test_sell_without_durable_position_is_rejected(setup):
    begin(setup)
    _, _, broker, state = setup
    with pytest.raises(SafetyHalt, match="no matching"):
        broker.sell(CONTRACT, 1)
    assert broker.trading.submissions == 0


def test_report_reads_durable_trades_without_sqlite(tmp_path, monkeypatch, capsys):
    import json
    import report
    snapshot = tmp_path / "report.json"
    snapshot.write_text(json.dumps({"state": {"trades": [{
        "worker": "test", "day": "2026-11-10", "pnl": -10,
        "exit_time": "2026-11-10T12:00:00+00:00", "exit_reason": "test",
    }]}}))
    monkeypatch.setattr("sys.argv", ["report.py", "--snapshot", str(snapshot)])
    report.main()
    assert "PAPER SCORECARD" in capsys.readouterr().out
