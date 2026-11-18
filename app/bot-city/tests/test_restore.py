from datetime import datetime, time
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from bots.config import WorkerConfig
from bots.errors import SafetyHalt
from bots.ledger import Ledger
from bots.options import Contract
from bots.worker import Position, Worker
from run_bots import restore

ET = ZoneInfo("America/New_York")


class FakeBroker:
    def __init__(self, positions):
        self.positions = positions
        self.cancel_called = False

    def now(self):
        return datetime(2026, 11, 10, 10, 0, tzinfo=ET)

    def cancel_all_open_orders(self):
        self.cancel_called = True

    def open_option_positions(self):
        return dict(self.positions)


class DummyBrokerForWorker:
    pass


def worker_config():
    return WorkerConfig(
        name="QQQ TEST",
        symbol="QQQ",
        strategy="vwap_cross",
        params={},
        dte=1,
        otm_steps=0,
        stop_loss_pct=0.2,
        take_profit_pct=1.0,
        trail_arm_pct=0.2,
        trail_giveback_pct=0.12,
        max_hold_minutes=60,
        cooldown_minutes=5,
        profit_brake=1500,
        max_worker_loss=600,
        max_trades=10,
        profit_lock_pct=0.3,
        profit_lock_after=500,
    )


def app_config():
    return SimpleNamespace(
        first_entry=time(9, 35),
        last_entry=time(15, 30),
        flatten=time(15, 50),
        dollars_per_trade=1000,
    )


def make_ledger(tmp_path: Path):
    return Ledger(
        db_path=str(tmp_path / "trades.db"),
        state_path=str(tmp_path / "state.json"),
        positions_path=str(tmp_path / "positions.json"),
        risk_path=str(tmp_path / "risk.json"),
        health_path=str(tmp_path / "health.json"),
    )


def test_restore_halts_on_untracked_broker_position(tmp_path):
    ledger = make_ledger(tmp_path)
    worker = Worker(worker_config(), app_config(), DummyBrokerForWorker(), ledger)
    ledger.attach([worker])
    broker = FakeBroker({"QQQ261111C00600000": 1})
    with pytest.raises(SafetyHalt):
        restore([worker], ledger, broker)
    assert broker.cancel_called
    ledger.close()


def test_restore_reconciles_saved_quantity_to_broker_truth(tmp_path):
    ledger = make_ledger(tmp_path)
    worker = Worker(worker_config(), app_config(), DummyBrokerForWorker(), ledger)
    ledger.attach([worker])
    now = datetime(2026, 11, 10, 10, 0, tzinfo=ET)
    contract = Contract("QQQ261111C00600000", "QQQ", "C", 600, now.date())
    worker.position = Position(contract, 3, 2.5, now, 0.0, 2.5, "test")
    worker.day = now.date()
    worker.day_peak = 700
    ledger.save_positions()
    ledger.save_risk_state()
    worker.position = None
    worker.day_peak = 0

    broker = FakeBroker({contract.symbol: 1})
    restore([worker], ledger, broker)
    assert worker.position is not None
    assert worker.position.qty == 1
    assert worker.day_peak == 700
    ledger.close()
