"""Read-only, deterministic analytics for closed trades; never contacts a broker.

Drawdown is absolute USD on chronologically closed trades, not mark-to-market.
Legacy rows retain unknown strategy/reconciliation rather than inventing evidence.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import json
import math


class JournalConflict(ValueError):
    """An identity was reused with different contents; do not silently count either."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def trade_id(row):
    if row.get("trade_id"):
        return str(row["trade_id"])
    # Backward compatible identity for pre-journal records. Conflicting outcomes
    # at the same natural key are errors, not two apparently successful trades.
    key = {k: row[k] for k in ("worker", "contract", "entry_time", "exit_time", "qty")}
    return "legacy-" + hashlib.sha256(canonical(key).encode()).hexdigest()


def unique_trades(rows):
    seen = {}
    for raw in rows:
        row = deepcopy(raw)
        identity = trade_id(row)
        row["trade_id"] = identity
        if identity in seen and canonical(seen[identity]) != canonical(row):
            raise JournalConflict(f"conflicting trade identity: {identity}")
        seen[identity] = row
    return list(seen.values())


def _instant(text):
    value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    # Legacy naive times use their supplied ordering; report flags the ambiguity.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def validated_trades(rows):
    rows = unique_trades(rows)
    for row in rows:
        date.fromisoformat(row["day"])
        if _instant(row["exit_time"]) < _instant(row["entry_time"]):
            raise ValueError("trade exits before entry")
        for key in ("pnl", "entry_price", "exit_price", "return_pct"):
            if isinstance(row[key], bool) or not isinstance(row[key], (int, float)) or not math.isfinite(row[key]):
                raise ValueError(f"non-finite trade {key}")
        if row["entry_price"] <= 0 or row["exit_price"] < 0:
            raise ValueError("invalid execution price")
        if isinstance(row["qty"], bool) or int(row["qty"]) != row["qty"] or row["qty"] <= 0:
            raise ValueError("trade quantity must be a positive integer")
    return sorted(rows, key=lambda r: _instant(r["exit_time"]))


def metrics(rows):
    rows = validated_trades(rows)
    pnl = [float(r["pnl"]) for r in rows]
    wins = [x for x in pnl if x > 0]
    losses = [x for x in pnl if x < 0]
    total, peak, drawdown = 0.0, 0.0, 0.0
    curve = []
    for row, value in zip(rows, pnl):
        total += value
        peak = max(peak, total)
        drawdown = max(drawdown, peak - total)
        curve.append({"time": row["exit_time"], "trade_id": row["trade_id"],
                      "realized_pnl": round(total, 2), "drawdown": round(peak-total, 2)})
    gross_profit, gross_loss = sum(wins), -sum(losses)
    return {"trades": len(rows), "wins": len(wins), "losses": len(losses),
            "breakeven": len(pnl)-len(wins)-len(losses),
            "realized_pnl": round(total, 2),
            "win_rate": len(wins)/len(pnl) if pnl else None,
            "expectancy": total/len(pnl) if pnl else None,
            "average_win": gross_profit/len(wins) if wins else None,
            "average_loss": -gross_loss/len(losses) if losses else None,
            "gross_profit": gross_profit, "gross_loss": gross_loss,
            "profit_factor": gross_profit/gross_loss if gross_loss else None,
            "profit_factor_status": "defined" if gross_loss else "no_losses" if pnl else "no_trades",
            "max_drawdown": round(drawdown, 2), "equity_curve": curve}


def build_report(rows, *, source=None, as_of=None):
    rows = validated_trades(rows)
    days, strategies, workers = defaultdict(list), defaultdict(list), defaultdict(list)
    for row in rows:
        days[row["day"]].append(row)
        strategies[row.get("strategy") or "unknown (legacy)"].append(row)
        workers[row["worker"]].append(row)
    return {"schema_version": 1, "generated_at": as_of or datetime.now(timezone.utc).isoformat(),
            "source": source or {"kind": "local-ledger"},
            "basis": "Closed trades, net of recorded fees; USD. No unrealized P&L or capital-adjusted returns. Days use recorded trading-session dates.",
            "warnings": [message for condition, message in (
                (any(not r.get("strategy") for r in rows), "Legacy trades have unknown strategy identity."),
                (any(datetime.fromisoformat(r["exit_time"].replace("Z", "+00:00")).tzinfo is None for r in rows), "Legacy timestamps lack timezone; ordering assumes UTC."),
                (any(r.get("reconciliation_status") not in {"checkpointed", "simulated"} for r in rows), "Some trades lack durable reconciliation evidence.")) if condition],
            "summary": metrics(rows),
            "daily": {k: metrics(v) for k, v in sorted(days.items())},
            "strategies": {k: metrics(v) for k, v in sorted(strategies.items())},
            "workers": {k: metrics(v) for k, v in sorted(workers.items())},
            "trades": rows}
