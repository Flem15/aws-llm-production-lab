from __future__ import annotations
from dataclasses import dataclass, field
from datetime import time
import yaml


def _t(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


@dataclass
class WorkerConfig:
    name: str
    symbol: str
    strategy: str
    params: dict
    dte: int
    otm_steps: int
    stop_loss_pct: float
    take_profit_pct: float | None
    trail_arm_pct: float
    trail_giveback_pct: float
    max_hold_minutes: int
    cooldown_minutes: int
    profit_brake: float | None      # None/null = no brake (the trend bot in video 3)
    max_worker_loss: float
    max_trades: int
    profit_lock_pct: float | None = None   # give back this % of the day's peak P&L → close & clock out
    profit_lock_after: float = 500         # the lock only arms once the day is up at least this much
    dollars_per_trade: float | None = None # per-worker override of account.dollars_per_trade


@dataclass
class Config:
    paper: bool
    dollars_per_trade: float
    max_daily_loss: float
    poll_seconds: int
    first_entry: time
    last_entry: time
    flatten: time
    workers: list[WorkerConfig]
    broker: str = "alpaca"
    limit_buffer: float = 0.05
    fill_timeout: int = 20
    backtest: dict = field(default_factory=dict)


def load_config(path: str = "config.yaml") -> Config:
    with open(path) as f:
        raw = yaml.safe_load(f)
    acct, sched, defaults = raw["account"], raw["schedule"], raw.get("defaults", {})
    workers = []
    for w in raw["workers"]:
        merged = {**defaults, **{k: v for k, v in w.items() if k not in ("name", "symbol", "strategy", "params")}}
        workers.append(WorkerConfig(
            name=w["name"], symbol=w["symbol"].upper(), strategy=w["strategy"],
            params=w.get("params") or {}, **merged))
    names = [w.name for w in workers]
    if len(names) != len(set(names)):
        raise ValueError("worker names must be unique")
    return Config(
        paper=bool(acct.get("paper", True)),
        dollars_per_trade=float(acct["dollars_per_trade"]),
        max_daily_loss=float(acct["max_daily_loss"]),
        poll_seconds=int(acct.get("poll_seconds", 15)),
        first_entry=_t(sched["first_entry"]), last_entry=_t(sched["last_entry"]),
        flatten=_t(sched["flatten"]), workers=workers, backtest=raw.get("backtest", {}),
        broker=str(acct.get("broker", "alpaca")).lower(),
        limit_buffer=float(acct.get("limit_buffer", 0.05)), fill_timeout=int(acct.get("fill_timeout", 20)),
    )
