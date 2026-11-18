from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date
import threading
from types import SimpleNamespace

import pytest

from bots.brokers import AlpacaBroker
from bots.lease import DynamoDbLease, LeaseLost, configured_lease
from bots.errors import SafetyHalt
from bots.options import Contract


class Clock:
    value = 0

    def __call__(self):
        return self.value


class AtomicTable:
    def __init__(self):
        self.item = None
        self.lock = threading.Lock()
        self.fail_after_put = False
        self.fail_update = False

    def put_item(self, **kwargs):
        with self.lock:
            if self.item is not None:
                raise RuntimeError("ConditionalCheckFailedException")
            self.item = deepcopy(kwargs['Item'])
            if self.fail_after_put:
                raise TimeoutError("response lost after successful write")

    def update_item(self, **kwargs):
        with self.lock:
            if self.fail_update:
                raise TimeoutError("renewal unavailable")
            self._check_owner(kwargs)
            self.item['heartbeat'] = kwargs['ExpressionAttributeValues'][':heartbeat']

    def delete_item(self, **kwargs):
        with self.lock:
            self._check_owner(kwargs)
            self.item = None

    def _check_owner(self, kwargs):
        if self.item is None or self.item['owner'] != kwargs['ExpressionAttributeValues'][':owner']:
            raise RuntimeError("ConditionalCheckFailedException")


def test_only_one_concurrent_owner_can_acquire():
    table = AtomicTable()
    leases = [DynamoDbLease(table, 'leases') for _ in range(12)]
    barrier = threading.Barrier(len(leases))

    def attempt(lease):
        barrier.wait()
        try:
            lease.acquire()
            return lease
        except LeaseLost:
            return None

    with ThreadPoolExecutor(max_workers=len(leases)) as executor:
        winners = [lease for lease in executor.map(attempt, leases) if lease]
    assert len(winners) == 1
    winners[0].release()
    assert table.item is None


def test_crashed_or_expired_owner_blocks_restart_until_operator_recovery():
    table, clock = AtomicTable(), Clock()
    old = DynamoDbLease(table, 'leases', clock=clock)
    old.acquire()
    clock.value = 31
    with pytest.raises(LeaseLost):
        old.renew()
    with pytest.raises(LeaseLost):
        DynamoDbLease(table, 'leases', clock=clock).acquire()
    with pytest.raises(LeaseLost):
        old.release()
    assert table.item['owner']['S'] == old.owner


def test_clean_release_allows_restart_with_new_owner():
    table = AtomicTable()
    old = DynamoDbLease(table, 'leases')
    old.acquire()
    old.release()
    new = DynamoDbLease(table, 'leases')
    new.acquire()
    assert new.owner != old.owner
    with pytest.raises(LeaseLost):
        old.release()
    assert table.item['owner']['S'] == new.owner
    new.release()


def test_renewal_extends_deadline_but_loss_is_permanent():
    table, clock, stop = AtomicTable(), Clock(), threading.Event()
    lease = DynamoDbLease(table, 'leases', clock=clock, on_loss=stop.set)
    lease.acquire()
    clock.value = 20
    lease.renew()
    clock.value = 35
    lease.assert_owned()
    table.fail_update = True
    with pytest.raises(LeaseLost):
        lease.renew()
    assert stop.is_set()
    table.fail_update = False
    with pytest.raises(LeaseLost):
        lease.renew()
    with pytest.raises(LeaseLost):
        lease.acquire()
    assert table.item is not None


def test_delayed_renewal_cannot_revive_expired_lease():
    table, clock = AtomicTable(), Clock()
    lease = DynamoDbLease(table, 'leases', clock=clock)
    lease.acquire()
    original = table.update_item

    def delayed(**kwargs):
        original(**kwargs)
        clock.value = 31

    table.update_item = delayed
    with pytest.raises(LeaseLost, match="expired during renewal"):
        lease.renew()
    with pytest.raises(LeaseLost):
        lease.assert_owned()


def test_ambiguous_acquisition_keeps_record_and_blocks_restart():
    table = AtomicTable()
    table.fail_after_put = True
    lease = DynamoDbLease(table, 'leases')
    with pytest.raises(LeaseLost, match="outcome unknown"):
        lease.acquire()
    assert table.item['owner']['S'] == lease.owner
    table.fail_after_put = False
    with pytest.raises(LeaseLost):
        DynamoDbLease(table, 'leases').acquire()


def test_stale_owner_cannot_delete_replacement_record():
    table = AtomicTable()
    lease = DynamoDbLease(table, 'leases')
    lease.acquire()
    table.item['owner'] = {'S': 'replacement'}
    with pytest.raises(LeaseLost):
        lease.release()
    assert table.item['owner']['S'] == 'replacement'


@pytest.mark.parametrize('mode', ['local', '', 'invalid'])
def test_ecs_cannot_disable_lease(monkeypatch, mode):
    monkeypatch.setenv('ECS_CONTAINER_METADATA_URI_V4', 'http://metadata')
    monkeypatch.setenv('BOT_CITY_LEASE_MODE', mode)
    with pytest.raises(SafetyHalt):
        configured_lease(threading.Event())


def test_distributed_mode_requires_table_and_region(monkeypatch):
    monkeypatch.setenv('BOT_CITY_LEASE_MODE', 'dynamodb')
    monkeypatch.delenv('BOT_CITY_LEASE_TABLE', raising=False)
    with pytest.raises(SafetyHalt, match='TABLE'):
        configured_lease(threading.Event())
    monkeypatch.setenv('BOT_CITY_LEASE_TABLE', 'leases')
    monkeypatch.delenv('AWS_REGION', raising=False)
    monkeypatch.delenv('AWS_DEFAULT_REGION', raising=False)
    with pytest.raises(SafetyHalt, match='REGION'):
        configured_lease(threading.Event())


def test_lost_lease_blocks_order_submission(monkeypatch):
    from test_alpaca_timeout import install_fake_alpaca_modules
    install_fake_alpaca_modules(monkeypatch)
    broker = object.__new__(AlpacaBroker)
    calls = []
    broker.trading = SimpleNamespace(submit_order=lambda request: calls.append(request))
    lease = DynamoDbLease(AtomicTable(), 'leases')
    broker.ownership_guard = lease.assert_owned
    contract = Contract('QQQ261120C00600000', 'QQQ', 'C', 600, date(2026, 11, 20))
    with pytest.raises(LeaseLost):
        broker._market_once(contract, 1, 'buy')
    assert not calls


def test_heartbeat_failure_stops_runtime():
    table = AtomicTable()
    stop = threading.Event()
    lease = DynamoDbLease(table, "leases", interval=0.01, on_loss=stop.set)
    lease.acquire()
    table.fail_update = True
    lease.start()
    try:
        assert stop.wait(2)
        with pytest.raises(LeaseLost):
            lease.assert_owned()
    finally:
        lease.stop()
    assert table.item is not None


def test_main_does_not_flatten_or_release_after_ownership_loss(monkeypatch):
    import run_bots
    table = AtomicTable()
    lease = DynamoDbLease(table, "leases")
    events = []
    worker = SimpleNamespace(position=object())
    ledger = SimpleNamespace(
        attach=lambda workers: None,
        write_health=lambda *args: events.append(args[0]),
        save_positions=lambda: None,
        save_risk_state=lambda: None,
        close=lambda: events.append("ledger_closed"),
    )
    monkeypatch.setattr("sys.argv", ["run_bots.py", "--no-dashboard"])
    monkeypatch.setattr(run_bots, "load_dotenv", lambda: None)
    monkeypatch.setattr(run_bots, "proxy_configuration", lambda: None)
    monkeypatch.setattr(run_bots, "load_config", lambda path: SimpleNamespace(
        broker="alpaca", paper=True, fill_timeout=20, workers=[object()]))
    monkeypatch.setattr(run_bots, "configure_logging", lambda: None)
    monkeypatch.setattr(run_bots, "validate_runtime_mode", lambda cfg: "paper-alpaca")
    monkeypatch.setattr(run_bots, "install_signal_handlers", lambda stop: None)
    monkeypatch.setattr(run_bots, "SingleInstanceLock", lambda: SimpleNamespace(
        acquire=lambda: None, release=lambda: events.append("local_released")))
    monkeypatch.setattr(run_bots, "configured_lease", lambda stop: lease)
    monkeypatch.setattr(run_bots, "AlpacaBroker", lambda **kwargs: SimpleNamespace())
    monkeypatch.setattr(run_bots, "configured_state", lambda *args: SimpleNamespace(
        start_session=lambda broker: None, halt=lambda detail: None))
    monkeypatch.setattr(run_bots, "DurableLedger", lambda *args: ledger)
    monkeypatch.setattr(run_bots, "Worker", lambda *args: worker)
    monkeypatch.setattr(run_bots, "restore", lambda *args: None)
    def lose(*args, **kwargs):
        table.fail_update = True
        lease.renew()
    monkeypatch.setattr(run_bots, "run_loop", lose)
    monkeypatch.setattr(run_bots, "close_managed_positions",
                        lambda *args: pytest.fail("lost owner must not flatten"))
    assert run_bots.main() == 2
    assert "safety_halt" in events
    assert "ledger_closed" in events
    assert table.item["owner"]["S"] == lease.owner
