"""Records every closed trade, persists recovery state, and writes dashboard/health snapshots."""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict
from datetime import datetime, date, timezone

from .options import Contract

COLS = [
    "worker", "day", "contract", "side", "qty", "entry_time", "entry_price", "exit_time",
    "exit_price", "pnl", "return_pct", "entry_reason", "exit_reason",
]


def _atomic_json(path: str, payload: dict) -> None:
    if not path:
        return
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as handle:
        json.dump(payload, handle, indent=2, default=str)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


class Ledger:
    def __init__(
        self,
        db_path="state/trades.db",
        state_path="dashboard/state.json",
        positions_path="state/positions.json",
        risk_path="state/risk.json",
        health_path="state/health.json",
        mode="paper",
    ):
        for p in (db_path, state_path, positions_path, risk_path, health_path):
            if p and os.path.dirname(p):
                os.makedirs(os.path.dirname(p), exist_ok=True)
        self.db_path = db_path
        self.state_path = state_path
        self.positions_path = positions_path
        self.risk_path = risk_path
        self.health_path = health_path
        self.mode = mode
        self.db = sqlite3.connect(db_path) if db_path else None
        if self.db:
            self.db.execute(f"CREATE TABLE IF NOT EXISTS trades ({', '.join(COLS)})")
            self.db.commit()
        self.trades: list = []
        self.workers: list = []

    def attach(self, workers):
        self.workers = workers

    def close(self):
        if self.db:
            self.db.close()
            self.db = None

    def record(self, trade):
        self.trades.append(trade)
        if self.db:
            d = asdict(trade)
            self.db.execute(
                f"INSERT INTO trades VALUES ({','.join('?' * len(COLS))})",
                [d[c] for c in COLS],
            )
            self.db.commit()

    def todays_trades(self, day: date):
        if not self.db:
            return []
        cur = self.db.execute("SELECT worker, pnl FROM trades WHERE day = ?", (str(day),))
        return cur.fetchall()

    def save_positions(self):
        if not self.positions_path:
            return
        out = {}
        for worker in self.workers:
            if worker.position:
                p = worker.position
                out[worker.name] = {
                    "contract": asdict(p.contract) | {"expiry": str(p.contract.expiry)},
                    "qty": p.qty,
                    "entry_price": p.entry_price,
                    "entry_time": p.entry_time.isoformat(),
                    "entry_fees": p.entry_fees,
                    "peak": p.peak,
                    "reason": p.reason,
                }
        _atomic_json(self.positions_path, out)

    def load_positions(self):
        if not self.positions_path or not os.path.exists(self.positions_path):
            return {}
        with open(self.positions_path) as handle:
            raw = json.load(handle)
        for value in raw.values():
            c = value["contract"]
            value["contract"] = Contract(
                c["symbol"], c["underlying"], c["kind"], float(c["strike"]), date.fromisoformat(c["expiry"])
            )
            value["entry_time"] = datetime.fromisoformat(value["entry_time"])
        return raw

    def save_risk_state(self):
        if not self.risk_path:
            return
        payload = {}
        for worker in self.workers:
            payload[worker.name] = {
                "day": str(worker.day) if worker.day else None,
                "day_peak": worker.day_peak,
                "off_reason": worker.off_reason,
                "cooldown_until": worker.cooldown_until.isoformat() if worker.cooldown_until else None,
            }
        _atomic_json(self.risk_path, payload)

    def load_risk_state(self):
        if not self.risk_path or not os.path.exists(self.risk_path):
            return {}
        with open(self.risk_path) as handle:
            return json.load(handle)

    def write_health(self, status: str, detail: str = ""):
        if not self.health_path:
            return
        _atomic_json(
            self.health_path,
            {
                "updated": datetime.now(timezone.utc).isoformat(),
                "status": status,
                "detail": detail,
                "mode": self.mode,
            },
        )

    def recent_trades(self, today):
        recent = [asdict(t) for t in self.trades if t.day == today][-30:]
        if not recent and self.db:
            cur = self.db.execute(
                "SELECT * FROM trades WHERE day = ? ORDER BY exit_time DESC LIMIT 30", (today,)
            )
            recent = [dict(zip(COLS, row)) for row in cur.fetchall()][::-1]
        return recent

    def write_state(self, now: datetime, market: str = "", extra: dict | None = None):
        self.save_positions()
        self.save_risk_state()
        self.write_health("running" if market.startswith("open") else "market_closed", market)

        if not self.state_path:
            return
        today = str(now.date())
        recent = self.recent_trades(today)
        state = {
            "updated": now.isoformat(),
            "mode": self.mode,
            "market": market,
            "vault_today": round(sum(w.day_pnl for w in self.workers), 2),
            "workers": [w.snapshot() for w in self.workers],
            "trades": recent,
        }
        if extra:
            state.update(extra)
        _atomic_json(self.state_path, state)


class DurableLedger(Ledger):
    """Commit fills, positions, risk and trade records together in DynamoDB."""
    def __init__(self, durable):
        super().__init__(db_path=None, positions_path=None, risk_path=None, mode="paper")
        self.durable = durable
        self.pending_trades = []

    def record(self, trade):
        self.pending_trades.append(asdict(trade))
        self.trades.append(trade)

    def todays_trades(self, day):
        return [(trade["worker"], trade["pnl"]) for trade in self.durable.snapshot["trades"]
                if trade["day"] == str(day)]

    def load_positions(self):
        from copy import deepcopy
        raw = deepcopy(self.durable.snapshot["positions"])
        for value in raw.values():
            contract = value["contract"]
            value["contract"] = Contract(
                contract["symbol"], contract["underlying"], contract["kind"],
                float(contract["strike"]), date.fromisoformat(contract["expiry"]))
            value["entry_time"] = datetime.fromisoformat(value["entry_time"])
        return raw

    def load_risk_state(self):
        from copy import deepcopy
        return deepcopy(self.durable.snapshot["risk"])

    def recent_trades(self, today):
        return [trade for trade in self.durable.snapshot["trades"] if trade["day"] == today][-30:]

    def save_positions(self):
        # Workers call save_risk_state immediately afterward, committing one snapshot.
        pass

    def save_risk_state(self):
        from copy import deepcopy
        positions, risk = {}, deepcopy(self.durable.snapshot["risk"])
        for worker in self.workers:
            if worker.position:
                position = worker.position
                positions[worker.name] = {
                    "contract": asdict(position.contract) | {"expiry": str(position.contract.expiry)},
                    "qty": position.qty, "entry_price": position.entry_price,
                    "entry_time": position.entry_time.isoformat(), "entry_fees": position.entry_fees,
                    "peak": position.peak, "reason": position.reason,
                }
            risk[worker.name] = {
                "day": str(worker.day) if worker.day else None,
                "day_peak": worker.day_peak, "off_reason": worker.off_reason,
                "cooldown_until": worker.cooldown_until.isoformat() if worker.cooldown_until else None,
            }
        self.durable.checkpoint(positions, risk, self.pending_trades)
        self.pending_trades.clear()
