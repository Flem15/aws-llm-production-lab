from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from bots.config import Config, WorkerConfig
from bots.options import Contract
from bots.worker import Position, Worker

ET = ZoneInfo("America/New_York")


class Broker:
    def option_mid(self, _contract):
        return 8.0


def make_worker():
    wc = WorkerConfig(
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
    cfg = SimpleNamespace(
        first_entry=datetime.strptime("09:35", "%H:%M").time(),
        last_entry=datetime.strptime("15:30", "%H:%M").time(),
        flatten=datetime.strptime("15:50", "%H:%M").time(),
        dollars_per_trade=1000,
    )
    return Worker(wc, cfg, Broker())


def test_account_equity_includes_open_loss():
    worker = make_worker()
    now = datetime(2026, 11, 10, 11, 0, tzinfo=ET)
    worker.day_pnl = -200
    worker.position = Position(
        Contract("QQQ261111C00600000", "QQQ", "C", 600, now.date()),
        1,
        10.0,
        now,
        0.0,
        10.0,
        "test",
    )
    worker.last_mark = 8.0
    assert worker.current_equity() == -400


def test_profit_lock_peak_survives_current_equity_updates():
    worker = make_worker()
    assert worker._lock_hit(1000) is False
    assert worker.day_peak == 1000
    assert worker._lock_hit(690) is True
