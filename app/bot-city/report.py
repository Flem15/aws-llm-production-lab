#!/usr/bin/env python3
"""Paper-trading scorecard from state/trades.db.

  python report.py                 # everything so far
  python report.py --days 7        # last 7 calendar days (the weekly check-in)
  python report.py --csv out.csv   # also export the trades

Shows, per worker and overall: trades, win rate, average win vs average loss, profit factor,
total P&L, worst day, max drawdown, and how trades closed. These are the numbers to judge by.
"""
import argparse
import json
from pathlib import Path
import sqlite3
from collections import Counter, defaultdict
from datetime import date, timedelta


def stats(rows):
    pnls = [r["pnl"] for r in rows]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    by_day = defaultdict(float)
    for r in rows:
        by_day[r["day"]] += r["pnl"]
    eq = peak = mdd = 0.0
    for d in sorted(by_day):
        eq += by_day[d]; peak = max(peak, eq); mdd = min(mdd, eq - peak)
    gross_loss = -sum(losses)
    return {
        "trades": len(pnls), "win%": (len(wins) / len(pnls) if pnls else 0),
        "avg_win": (sum(wins) / len(wins) if wins else 0), "avg_loss": (sum(losses) / len(losses) if losses else 0),
        "pf": (sum(wins) / gross_loss if gross_loss else float("inf") if wins else 0),
        "total": sum(pnls), "days": len(by_day), "green": sum(v > 0 for v in by_day.values()),
        "worst_day": min(by_day.values()) if by_day else 0, "mdd": mdd,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="state/trades.db")
    ap.add_argument("--snapshot", help="Day 17 recovery report JSON, read locally")
    ap.add_argument("--days", type=int, help="only the last N calendar days")
    ap.add_argument("--csv", help="export the selected trades to this CSV")
    a = ap.parse_args()
    if a.snapshot:
        payload = json.loads(Path(a.snapshot).read_text())
        rows = payload["state"]["trades"]
        if a.days:
            cutoff = str(date.today() - timedelta(days=a.days))
            rows = [row for row in rows if row["day"] >= cutoff]
        rows = sorted(rows, key=lambda row: row["exit_time"])
    else:
        with sqlite3.connect(a.db) as con:
            con.row_factory = sqlite3.Row
            q, args = "SELECT * FROM trades", ()
            if a.days:
                q, args = q + " WHERE day >= ?", (str(date.today() - timedelta(days=a.days)),)
            rows = [dict(r) for r in con.execute(q + " ORDER BY exit_time", args)]
    if not rows:
        raise SystemExit("No closed trades yet. Let the bots run through a market day first.")

    span = f"{rows[0]['day']} → {rows[-1]['day']}"
    print(f"\n{'═' * 92}\n  PAPER SCORECARD · {span}\n{'═' * 92}")
    hdr = f"  {'WORKER':<12}{'TRADES':>7}{'WIN%':>6}{'AVG WIN':>10}{'AVG LOSS':>10}{'PROF.F':>8}{'TOTAL':>11}{'GREEN DAYS':>12}{'WORST DAY':>11}{'MAX DD':>10}"
    print(hdr)
    groups = defaultdict(list)
    for r in rows:
        groups[r["worker"]].append(r)
    for name in sorted(groups):
        s = stats(groups[name])
        print(f"  {name:<12}{s['trades']:>7}{s['win%']:>6.0%}{s['avg_win']:>+10,.0f}{s['avg_loss']:>+10,.0f}"
              f"{s['pf']:>8.2f}{s['total']:>+11,.0f}{s['green']:>7}/{s['days']:<4}{s['worst_day']:>+11,.0f}{s['mdd']:>+10,.0f}")
    s = stats(rows)
    print(f"  {'─' * 90}\n  {'ALL':<12}{s['trades']:>7}{s['win%']:>6.0%}{s['avg_win']:>+10,.0f}{s['avg_loss']:>+10,.0f}"
          f"{s['pf']:>8.2f}{s['total']:>+11,.0f}{s['green']:>7}/{s['days']:<4}{s['worst_day']:>+11,.0f}{s['mdd']:>+10,.0f}")

    print("\n  HOW TRADES CLOSED")
    for name in sorted(groups):
        c = Counter(r["exit_reason"] for r in groups[name])
        print(f"  {name:<12}" + " · ".join(f"{k} {v}" for k, v in c.most_common()))
    print("\n  Profit factor = gross wins ÷ gross losses. Above 1.3 over 100+ trades is the bar worth aiming for.\n")
    if a.csv:
        import csv
        with open(a.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader(); w.writerows(rows)
        print(f"  trades → {a.csv}\n")


if __name__ == "__main__":
    main()
