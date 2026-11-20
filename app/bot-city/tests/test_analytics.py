from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import json
import sqlite3

import pytest
from bots.analytics import build_report, metrics, unique_trades, JournalConflict
from bots.ledger import Ledger, COLS
from bots.worker import Trade


def row(i=1, pnl=10):
    return dict(worker="alpha", day="2026-11-20", contract="SYNTHETIC", side="CALL", qty=1,
        entry_time=f"2026-11-20T10:0{i}:00+00:00", entry_price=1.0,
        exit_time=f"2026-11-20T11:0{i}:00+00:00", exit_price=1.0+pnl/100,
        pnl=pnl, return_pct=pnl/100, entry_reason="synthetic entry", exit_reason="synthetic exit",
        trade_id=f"synthetic-{i}", strategy="vwap_cross", reconciliation_status="simulated")


def test_metrics_include_intraday_drawdown_and_breakevens():
    result = metrics([row(3, 0), row(2, -50), row(1, 100), row(4, 10)])
    assert result["realized_pnl"] == 60
    assert result["max_drawdown"] == 50
    assert result["win_rate"] == .5
    assert result["expectancy"] == 15
    assert result["profit_factor"] == 2.2
    assert result["average_loss"] == -50
    assert result["breakeven"] == 1


@pytest.mark.parametrize("rows,status", [([], "no_trades"), ([row(1)], "no_losses"), ([row(1, 0)], "no_losses")])
def test_empty_and_no_loss_metrics_are_json_safe(rows, status):
    report = build_report(rows)
    assert report["summary"]["profit_factor"] is None
    assert report["summary"]["profit_factor_status"] == status
    json.dumps(report, allow_nan=False)


def test_duplicate_replay_and_conflicting_identity():
    assert metrics([row(), row()])["trades"] == 1
    with pytest.raises(JournalConflict):
        metrics([row(), row(pnl=99)])


@pytest.mark.parametrize("field,value", [("pnl", float("nan")), ("pnl", float("inf")), ("qty", 0), ("qty", 1.5)])
def test_invalid_analytics_rejected(field, value):
    record = row(); record[field] = value
    with pytest.raises((ValueError, TypeError)):
        metrics([record])


def test_strategy_and_day_groups_preserve_every_record():
    records = [row(1), row(2, -5), row(3, 30)]
    records[2].update(strategy="trend", day="2026-11-21", exit_time="2026-11-21T11:00:00+00:00")
    report = build_report(records)
    assert report["strategies"]["trend"]["realized_pnl"] == 30
    assert sum(x["trades"] for x in report["daily"].values()) == 3
    assert sum(x["realized_pnl"] for x in report["workers"].values()) == 35


def ledger(path):
    return Ledger(str(path), state_path=None, positions_path=None, risk_path=None, health_path=None)


def test_sqlite_restart_replay_keeps_one_trade(tmp_path):
    path = tmp_path / "trades.db"
    first = ledger(path)
    trade = Trade(**row())
    first.record(trade); first.close()
    second = ledger(path)
    second.record(trade)
    assert len(second.history()) == 1
    assert second.db.execute("SELECT count(*) FROM trades").fetchone()[0] == 1
    changed = Trade(**row(pnl=12))
    with pytest.raises(JournalConflict):
        second.record(changed)
    assert len(second.history()) == 1
    second.close()


def test_legacy_additive_migration_preserves_original_rows(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as db:
        db.execute(f"CREATE TABLE trades ({','.join(COLS)})")
        db.execute(f"INSERT INTO trades VALUES ({','.join('?'*len(COLS))})", [row()[k] for k in COLS])
    instance = ledger(path)
    assert len(instance.history()) == 1
    assert instance.db.execute("SELECT count(*) FROM trades").fetchone()[0] == 1
    assert build_report(instance.history())["warnings"]
    instance.close()


def test_daily_reports_generated_from_committed_history(tmp_path):
    instance = ledger(tmp_path / "trades.db")
    instance.state_path = str(tmp_path / "dashboard/state.json")
    instance.record(Trade(**row()))
    instance.write_state(datetime(2026,11,20,tzinfo=timezone.utc))
    report = json.loads((tmp_path / "dashboard/daily/2026-11-20.json").read_text())
    assert report["summary"]["trades"] == 1
    instance.close()
    restarted = ledger(tmp_path / "trades.db")
    assert restarted.history()[0]["strategy"] == "vwap_cross"
    restarted.close()


def test_export_rejects_display_only_and_unreconciled_state(tmp_path):
    from analytics_report import generate
    from bots.errors import SafetyHalt
    snapshot = tmp_path / "state.json"
    snapshot.write_text(json.dumps({"trades": [row()]}))
    with pytest.raises(ValueError):
        generate(snapshot, tmp_path / "out")
    snapshot.write_text(json.dumps({"state": {"trades": [row()]}}))
    with pytest.raises(SafetyHalt):
        generate(snapshot, tmp_path / "out")


def test_empty_day_report_and_no_false_performance(tmp_path):
    instance = ledger(tmp_path / "empty.db")
    instance.state_path = str(tmp_path / "dashboard/state.json")
    instance.write_state(datetime(2026,11,20,tzinfo=timezone.utc))
    daily = json.loads((tmp_path / "dashboard/daily/2026-11-20.json").read_text())
    assert daily["summary"]["trades"] == 0
    assert daily["summary"]["expectancy"] is None
    instance.close()


def test_conflicting_legacy_migration_leaves_source_untouched(tmp_path):
    path = tmp_path / "legacy-conflict.db"
    with sqlite3.connect(path) as db:
        db.execute(f"CREATE TABLE trades ({','.join(COLS)})")
        for value in (10,20):
            db.execute(f"INSERT INTO trades VALUES ({','.join('?'*len(COLS))})", [row(pnl=value)[k] for k in COLS])
    for _ in range(2):
        with pytest.raises(JournalConflict):
            ledger(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM trades").fetchone()[0] == 2
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='trade_journal'").fetchone()


def test_same_instant_preserves_committed_ledger_order():
    first, second = row(1,-50), row(2,100)
    second['exit_time'] = first['exit_time']
    first['trade_id'], second['trade_id'] = 'z-first', 'a-second'
    result = metrics([first, second])
    assert [p['trade_id'] for p in result['equity_curve']] == ['z-first','a-second']
    assert result['max_drawdown'] == 50
