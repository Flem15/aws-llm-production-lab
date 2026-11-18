"""One strategy worker per ticker, with paper-trading risk controls and restart-safe state."""
from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

from .config import Config, WorkerConfig
from .errors import OrderStateUnknown, SafetyHalt
from .options import Contract
from .strategies import make_strategy


@dataclass
class Position:
    contract: Contract
    qty: int
    entry_price: float
    entry_time: datetime
    entry_fees: float
    peak: float
    reason: str


@dataclass
class Trade:
    worker: str
    day: str
    contract: str
    side: str
    qty: int
    entry_time: str
    entry_price: float
    exit_time: str
    exit_price: float
    pnl: float
    return_pct: float
    entry_reason: str
    exit_reason: str


class Worker:
    def __init__(self, wc: WorkerConfig, cfg: Config, broker, ledger=None):
        self.wc, self.cfg, self.broker, self.ledger = wc, cfg, broker, ledger
        self.strategy = make_strategy(wc.strategy, wc.params, wc.symbol)
        self.position: Position | None = None
        self.day = None
        self.day_pnl = 0.0
        self.day_peak = 0.0
        self.trades_today = 0
        self.wins_today = 0
        self.off_reason: str | None = None
        self.cooldown_until: datetime | None = None
        self.last_bar = None
        self.last_mark: float | None = None
        self.thoughts: deque = deque(maxlen=40)

    @property
    def name(self):
        return self.wc.name

    def think(self, now: datetime, text: str):
        self.thoughts.append((now.strftime("%H:%M"), text))
        logging.getLogger("worker").info("[%s] %s", self.name, text)

    def _roll_day(self, now: datetime):
        day = now.date()
        if day != self.day:
            self.day = day
            self.day_pnl = 0.0
            self.day_peak = 0.0
            self.trades_today = 0
            self.wins_today = 0
            self.off_reason = None
            self.cooldown_until = None
            self.last_bar = None
            self.last_mark = None
            self.thoughts.clear()

    def status(self) -> str:
        if self.off_reason:
            return f"off duty · {self.off_reason}"
        if self.position:
            return f"in trade · {self.position.contract.kind}"
        return "watching"

    def step(self, now: datetime, frame, session=None):
        self._roll_day(now)
        if self.position:
            self._manage(now, session)
        if frame is None or frame.empty:
            return
        bar_ts = frame.index[-1]
        if bar_ts == self.last_bar:
            return
        self.last_bar = bar_ts
        if not self.position and self._can_enter(now, session):
            signal = self.strategy.signal(frame)
            if self.strategy.note:
                self.think(now, self.strategy.note)
                self.strategy.note = None
            if signal:
                self._enter(now, signal, float(frame["close"].iloc[-1]))

    def _can_enter(self, now: datetime, session=None) -> bool:
        if self.off_reason:
            return False
        if session is None:
            allowed = self.cfg.first_entry <= now.time() < self.cfg.last_entry
        else:
            allowed = session.first_entry <= now < session.last_entry
        if not allowed:
            return False
        if self.cooldown_until and now < self.cooldown_until:
            return False
        return True

    def _enter(self, now, signal, price):
        kind = "C" if signal.side == "CALL" else "P"
        contract = self.broker.pick_contract(
            self.wc.symbol, kind, price, self.wc.dte, self.wc.otm_steps
        )
        if contract is None:
            return
        mid = self.broker.option_mid(contract)
        if mid <= 0.05:
            self.think(now, f"{signal.reason} · skipped, no usable quote")
            return
        budget = self.wc.dollars_per_trade or self.cfg.dollars_per_trade
        qty = math.floor(budget / (mid * 100))
        if qty < 1:
            self.think(now, f"{signal.reason} · skipped, 1 contract (${mid*100:.0f}) > budget")
            return
        try:
            self.broker.worker_name = self.name
            fill, fees = self.broker.buy(contract, qty)
        except SafetyHalt:
            raise
        except Exception as exc:
            self.think(now, f"{signal.reason} · order failed: {exc}")
            self.cooldown_until = now + timedelta(minutes=self.wc.cooldown_minutes)
            return
        qty = getattr(self.broker, "last_filled_qty", None) or qty
        self.broker.last_filled_qty = None
        self.position = Position(contract, qty, fill, now, fees, fill, signal.reason)
        self.last_mark = fill
        self.think(
            now,
            f"FIRE {signal.side} ×{qty} @ {fill:.2f} "
            f"({contract.underlying} {contract.strike:g}{contract.kind} {contract.expiry:%m/%d}) · {signal.reason}",
        )
        if self.ledger:
            self.ledger.save_positions()
            self.ledger.save_risk_state()

    def _exit_reason(self, now, px, session=None) -> str | None:
        position, worker_cfg = self.position, self.wc
        position.peak = max(position.peak, px)
        change = (px - position.entry_price) / position.entry_price
        peak_gain = (position.peak - position.entry_price) / position.entry_price
        flatten_due = (
            now >= session.flatten if session is not None else now.time() >= self.cfg.flatten
        )
        if flatten_due:
            return "end of day"
        if change <= -worker_cfg.stop_loss_pct:
            return "stopped out"
        if worker_cfg.take_profit_pct and change >= worker_cfg.take_profit_pct:
            return "profit locked"
        if (
            peak_gain >= worker_cfg.trail_arm_pct
            and px <= position.peak * (1 - worker_cfg.trail_giveback_pct)
        ):
            return "rode the trail"
        if now - position.entry_time >= timedelta(minutes=worker_cfg.max_hold_minutes):
            return "time stop"
        return None

    def unrealized(self, px) -> float:
        position = self.position
        return (
            (px - position.entry_price) * position.qty * 100 - position.entry_fees
            if position
            else 0.0
        )

    def current_equity(self) -> float:
        if not self.position:
            return self.day_pnl
        px = self.last_mark
        if px is None:
            px = self.broker.option_mid(self.position.contract)
            self.last_mark = px
        return self.day_pnl + self.unrealized(px)

    def _lock_hit(self, equity: float) -> bool:
        worker_cfg = self.wc
        self.day_peak = max(self.day_peak, equity)
        return (
            bool(worker_cfg.profit_lock_pct)
            and self.day_peak >= worker_cfg.profit_lock_after
            and equity <= self.day_peak * (1 - worker_cfg.profit_lock_pct)
        )

    def _manage(self, now, session=None):
        px = self.broker.option_mid(self.position.contract)
        self.last_mark = px
        reason = self._exit_reason(now, px, session)
        if not reason and self._lock_hit(self.day_pnl + self.unrealized(px)):
            reason = "profit lock"
        if reason:
            self.close(now, reason)

    def close(self, now, reason):
        position = self.position
        try:
            self.broker.worker_name = self.name
            fill, fees = self.broker.sell(position.contract, position.qty)
        except SafetyHalt:
            raise
        pnl = (fill - position.entry_price) * position.qty * 100 - position.entry_fees - fees
        return_pct = (fill - position.entry_price) / position.entry_price
        self.position = None
        self.last_mark = None
        self.day_pnl += pnl
        self.trades_today += 1
        self.wins_today += pnl > 0
        self.cooldown_until = now + timedelta(minutes=self.wc.cooldown_minutes)
        emoji = "✅" if pnl > 0 else "🔴"
        self.think(
            now,
            f"{emoji} closed ×{position.qty} @ {fill:.2f} · {reason} · {pnl:+,.0f} ({return_pct:+.1%})",
        )
        trade = Trade(
            self.name,
            str(now.date()),
            position.contract.symbol,
            "CALL" if position.contract.kind == "C" else "PUT",
            position.qty,
            position.entry_time.isoformat(),
            position.entry_price,
            now.isoformat(),
            fill,
            round(pnl, 2),
            round(return_pct, 4),
            position.reason,
            reason,
        )
        if self.ledger:
            self.ledger.record(trade)
            self.ledger.save_positions()
            self.ledger.save_risk_state()
        self._check_brakes()
        return trade

    def _check_brakes(self):
        worker_cfg = self.wc
        if self._lock_hit(self.day_pnl):
            self.off_reason = (
                f"profit lock · kept {self.day_pnl:+,.0f} of {self.day_peak:+,.0f}"
            )
        elif worker_cfg.profit_brake is not None and self.day_pnl >= worker_cfg.profit_brake:
            self.off_reason = "profit brake hit"
        elif self.day_pnl <= -worker_cfg.max_worker_loss:
            self.off_reason = "loss limit hit"
        elif self.trades_today >= worker_cfg.max_trades:
            self.off_reason = "max trades"

    def turn_off(self, reason):
        self.off_reason = reason
        if self.ledger:
            self.ledger.save_risk_state()

    def snapshot(self) -> dict:
        pos = None
        if self.position:
            position = self.position
            mark = self.last_mark or position.entry_price
            pos = {
                "contract": position.contract.symbol,
                "side": "CALL" if position.contract.kind == "C" else "PUT",
                "qty": position.qty,
                "entry": position.entry_price,
                "peak": position.peak,
                "since": position.entry_time.strftime("%H:%M"),
                "entry_time": position.entry_time.isoformat(),
                "mark": mark,
                "open_pnl": round(self.unrealized(mark), 2),
            }
        return {
            "name": self.name,
            "symbol": self.wc.symbol,
            "strategy": self.wc.strategy,
            "status": self.status(),
            "off": bool(self.off_reason),
            "pnl": round(self.day_pnl, 2),
            "peak": round(self.day_peak, 2),
            "brake": self.wc.profit_brake,
            "lock": self.wc.profit_lock_pct,
            "trades": self.trades_today,
            "wins": self.wins_today,
            "position": pos,
            "thoughts": list(self.thoughts)[-12:],
        }
