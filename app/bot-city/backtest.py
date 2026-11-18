#!/usr/bin/env python3
"""Replay past 1-minute bars through the same Worker code the live bot uses.

  python backtest.py --demo                    # fake data, no keys needed (tests the plumbing)
  python backtest.py --alpaca --days 23        # real QQQ/SPY/IWM bars from Alpaca
  python backtest.py --csv QQQ=data/qqq.csv --csv SPY=data/spy.csv

Option prices are SIMULATED with Black-Scholes at a fixed IV, plus slippage. Real 0DTE/1DTE
fills, IV crush and spreads differ, so treat results as a rough sanity check, not a promise.
"""
import argparse
import csv
from collections import defaultdict
from dataclasses import asdict

import pandas as pd

from bots.config import load_config
from bots.brokers import SimBroker
from bots.ledger import Ledger
from bots.worker import Worker
from bots import data as D
from bots.chart import chart_payload


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--demo", action="store_true", help="synthetic random data")
    src.add_argument("--alpaca", action="store_true", help="download bars from Alpaca")
    src.add_argument("--csv", action="append", metavar="SYM=path", help="CSV per symbol")
    ap.add_argument("--days", type=int, default=23)
    ap.add_argument("--out", default="backtest_trades.csv")
    args = ap.parse_args()

    cfg = load_config(args.config)
    symbols = sorted({w.symbol for w in cfg.workers})
    if args.demo:
        bars = {s: D.synthetic(s, args.days) for s in symbols}
    elif args.alpaca:
        from dotenv import load_dotenv; load_dotenv()
        bars = {s: D.load_alpaca(s, args.days) for s in symbols}
    else:
        paths = dict(x.split("=", 1) for x in args.csv)
        bars = {s.upper(): D.load_csv(p) for s, p in paths.items()}
        missing = set(symbols) - set(bars)
        if missing:
            raise SystemExit(f"no CSV for {missing}")

    bt = cfg.backtest
    broker = SimBroker(bt.get("iv", {}), bt.get("default_iv", 0.2), bt.get("slippage_pct", 0.02), bt.get("fee_per_contract", 0.05))
    ledger = Ledger(db_path=None, state_path="dashboard/state.json", positions_path=None, mode="backtest")
    workers = [Worker(wc, cfg, broker, ledger) for wc in cfg.workers]
    ledger.attach(workers)

    prepared = {w.name: w.strategy.prepare(bars[w.wc.symbol]) for w in workers}
    timeline = sorted(set().union(*[set(b.index) for b in bars.values()]))
    pos = {w.name: 0 for w in workers}
    closes = {s: bars[s]["close"] for s in symbols}
    daily = defaultdict(lambda: defaultdict(float))

    for i, ts in enumerate(timeline):
        # a bar stamped 10:00 closes at 10:01, that's when the bot "sees" it
        now = ts + pd.Timedelta(minutes=1)
        prices = {s: float(closes[s].get(ts, float("nan"))) for s in symbols}
        broker.set_market(now, {s: p for s, p in prices.items() if p == p})
        for w in workers:
            df = prepared[w.name]
            j = pos[w.name]
            while j < len(df) and df.index[j] <= ts:
                j += 1
            pos[w.name] = j
            w.step(now, df.iloc[:j] if j else None)
        # account-level kill switch
        if sum(w.day_pnl for w in workers) <= -cfg.max_daily_loss:
            for w in workers:
                if w.position:
                    w.close(now, "account loss limit")
                w.turn_off("account loss limit") if not w.off_reason else None
        last_of_day = i + 1 == len(timeline) or timeline[i + 1].date() != ts.date()
        if last_of_day:
            for w in workers:
                if w.position:
                    w.close(now, "end of day")
                daily[str(ts.date())][w.name] = w.day_pnl

    trades = ledger.trades
    with open(args.out, "w", newline="") as f:
        if trades:
            wr = csv.DictWriter(f, fieldnames=list(asdict(trades[0])))
            wr.writeheader()
            wr.writerows(asdict(t) for t in trades)

    # ── report ──
    print(f"\n{'═'*64}\n  BACKTEST · {len(daily)} trading days · {'DEMO (fake data)' if args.demo else 'real bars, simulated options'}\n{'═'*64}")
    print(f"  {'WORKER':<12}{'TRADES':>8}{'WINS':>7}{'WIN%':>7}{'EARNED':>12}{'AVG/TRADE':>12}")
    for w in workers:
        ts_ = [t for t in trades if t.worker == w.name]
        wins = sum(t.pnl > 0 for t in ts_)
        tot = sum(t.pnl for t in ts_)
        print(f"  {w.name:<12}{len(ts_):>8}{wins:>7}{(wins/len(ts_) if ts_ else 0):>7.0%}{tot:>+12,.0f}{(tot/len(ts_) if ts_ else 0):>+12,.0f}")
    day_tot = {d: sum(v.values()) for d, v in daily.items()}
    total = sum(day_tot.values())
    green = sum(v > 0 for v in day_tot.values())
    eq, peak, mdd = 0, 0, 0
    for d in sorted(day_tot):
        eq += day_tot[d]; peak = max(peak, eq); mdd = min(mdd, eq - peak)
    print(f"{'─'*64}\n  VAULT TOTAL  {total:+,.0f}   ·  green days {green}/{len(day_tot)}  ·  max drawdown {mdd:,.0f}")
    if day_tot:
        best = max(day_tot, key=day_tot.get); worst = min(day_tot, key=day_tot.get)
        print(f"  best day {best} {day_tot[best]:+,.0f}  ·  worst day {worst} {day_tot[worst]:+,.0f}")
    print(f"  trades → {args.out}\n")
    ledger.write_state(timeline[-1].to_pydatetime(), market="backtest finished",
                       extra={"days": {d: round(v, 2) for d, v in sorted(day_tot.items())},
                              "vault_total": round(total, 2),
                              "payroll": {w.name: {"t": sum(t.worker == w.name for t in trades),
                                                   "w": sum(t.worker == w.name and t.pnl > 0 for t in trades),
                                                   "p": round(sum(t.pnl for t in trades if t.worker == w.name), 2)}
                                          for w in workers},
                              "charts": {s: chart_payload(bars[s]) for s in symbols}})


if __name__ == "__main__":
    main()
