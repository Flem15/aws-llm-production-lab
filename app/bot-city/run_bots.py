#!/usr/bin/env python3
"""Run Bot City in a fail-closed paper-trading mode."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import logging
import os
import signal
import sys
import threading
import time

from dotenv import load_dotenv

from bots.brokers import AlpacaBroker, WebullBroker
from bots.chart import chart_payload
from bots.config import load_config
from bots.dashboard import serve_in_background
from bots.errors import OrderStateUnknown, SafetyHalt
from bots.ledger import Ledger, DurableLedger
from bots.transport import proxy_configuration
from bots.durable import configured_state
from bots.lease import configured_lease
from bots.logging_utils import configure_logging
from bots.runtime import session_schedule, validate_runtime_mode
from bots.singleton import SingleInstanceLock
from bots.worker import Position, Worker

log = logging.getLogger("bots")


def restore(workers, ledger, broker):
    """Fail-closed restart recovery against broker truth, not only local files."""
    now = broker.now()
    totals = defaultdict(float)
    counts = defaultdict(int)
    wins = defaultdict(int)
    for name, pnl in ledger.todays_trades(now.date()):
        totals[name] += pnl
        counts[name] += 1
        wins[name] += int(pnl > 0)

    if isinstance(ledger, DurableLedger) and set(counts) - {worker.name for worker in workers}:
        raise SafetyHalt("today's trades reference unconfigured workers; risk review required")
    if not isinstance(ledger, DurableLedger) and hasattr(broker, "cancel_all_open_orders"):
        broker.cancel_all_open_orders()

    try:
        held = broker.open_option_positions()
    except Exception as exc:
        raise SafetyHalt("could not reconcile broker positions at startup") from exc

    if held is None:
        raise SafetyHalt("broker position response could not be parsed; refusing to trade")

    saved = ledger.load_positions()
    if isinstance(ledger, DurableLedger) and set(saved) - {worker.name for worker in workers}:
        raise SafetyHalt("durable positions reference unconfigured workers")
    risk = ledger.load_risk_state()
    if isinstance(ledger, DurableLedger):
        symbols = [p["contract"].symbol for p in saved.values()]
        if len(set(symbols)) != len(symbols):
            raise SafetyHalt("multiple workers share a contract; operator allocation required")

    for worker in workers:
        worker._roll_day(now)
        worker.day_pnl = totals[worker.name]
        worker.trades_today = counts[worker.name]
        worker.wins_today = wins[worker.name]

        saved_risk = risk.get(worker.name, {})
        if saved_risk.get("day") == str(now.date()):
            worker.day_peak = max(worker.day_pnl, float(saved_risk.get("day_peak") or 0))
            worker.off_reason = saved_risk.get("off_reason") or None
            cooldown = saved_risk.get("cooldown_until")
            if cooldown:
                worker.cooldown_until = datetime.fromisoformat(cooldown)
        else:
            worker.day_peak = max(worker.day_peak, worker.day_pnl)

        saved_position = saved.get(worker.name)
        if saved_position:
            symbol = saved_position["contract"].symbol
            actual_qty = held.get(symbol, 0)
            if isinstance(ledger, DurableLedger) and actual_qty != saved_position["qty"]:
                raise SafetyHalt("saved position differs from broker; operator recovery required")
            if actual_qty > 0:
                saved_position["qty"] = actual_qty
                worker.position = Position(**saved_position)
                worker.last_mark = worker.position.entry_price
                log.info("%s: re-adopted %s x%d", worker.name, symbol, actual_qty)
            else:
                log.warning(
                    "%s: saved position %s is absent at broker; dropping stale local state",
                    worker.name,
                    symbol,
                )
        worker._check_brakes()

    tracked = {
        worker.position.contract.symbol
        for worker in workers
        if worker.position
    }
    untracked = set(held) - tracked
    if untracked:
        raise SafetyHalt(
            "untracked broker positions detected: " + ", ".join(sorted(untracked))
        )

    ledger.save_positions()
    ledger.save_risk_state()


def close_managed_positions(workers, now, reason):
    failures = []
    for worker in workers:
        if not worker.position:
            continue
        try:
            worker.close(now, reason)
        except Exception as exc:
            failures.append(f"{worker.name}: {exc}")
            log.exception("%s failed to close during %s", worker.name, reason)
    if failures:
        raise SafetyHalt("one or more managed positions failed to close: " + " | ".join(failures))


def account_equity(workers) -> float:
    return sum(worker.current_equity() for worker in workers)


def install_signal_handlers(stop_event: threading.Event):
    def request_stop(signum, _frame):
        log.warning("shutdown requested by signal %s", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)


def run_loop(cfg, broker, ledger, workers, stop_event, lease=None):
    symbols = sorted({worker.wc.symbol for worker in workers})
    last_minute = None
    frames = {}
    charts = {}

    while not stop_event.is_set():
        try:
            if lease:
                lease.assert_owned()
            clock = broker.clock()
            now = broker.now()

            if not clock.is_open:
                if any(worker.position for worker in workers):
                    raise SafetyHalt(
                        "market is closed while a managed position is still open; operator review required"
                    )
                ledger.write_state(
                    now,
                    market=f"closed · opens {clock.next_open:%a %H:%M} ET",
                )
                sleep_for = min(
                    300,
                    max(15, (clock.next_open - clock.timestamp).total_seconds()),
                )
                stop_event.wait(sleep_for)
                continue

            session = session_schedule(now, clock.next_close, cfg)

            if now >= session.flatten:
                close_managed_positions(workers, now, "session flatten")
                for worker in workers:
                    if not worker.off_reason:
                        worker.turn_off("session flattened")
                ledger.write_state(
                    now,
                    market=(
                        f"open · flattened {session.flatten:%H:%M} ET · "
                        f"close {session.market_close:%H:%M} ET"
                    ),
                    extra={"charts": charts},
                )
                stop_event.wait(cfg.poll_seconds)
                continue

            minute = now.replace(second=0, microsecond=0)
            if minute != last_minute and now.second >= 3:
                bars = {symbol: broker.minute_bars(symbol) for symbol in symbols}
                charts = {symbol: chart_payload(df) for symbol, df in bars.items()}
                frames = {
                    worker.name: (
                        worker.strategy.prepare(bars[worker.wc.symbol])
                        if not bars[worker.wc.symbol].empty
                        else None
                    )
                    for worker in workers
                }
                last_minute = minute

            for worker in workers:
                try:
                    worker.step(now, frames.get(worker.name), session=session)
                except SafetyHalt:
                    raise
                except Exception:
                    log.exception("%s step failed", worker.name)

            equity = account_equity(workers)
            if equity <= -cfg.max_daily_loss:
                close_managed_positions(workers, now, "account loss limit")
                for worker in workers:
                    if not worker.off_reason:
                        worker.turn_off("account loss limit")

            ledger.write_state(
                now,
                market=(
                    f"open · last entry {session.last_entry:%H:%M} ET · "
                    f"flatten {session.flatten:%H:%M} ET"
                ),
                extra={
                    "charts": charts,
                    "account_equity": round(equity, 2),
                    "session_close": session.market_close.isoformat(),
                    "session_flatten": session.flatten.isoformat(),
                },
            )
            stop_event.wait(cfg.poll_seconds)

        except SafetyHalt:
            raise
        except Exception:
            log.exception("loop error; retrying in 30s")
            stop_event.wait(30)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--no-dashboard", action="store_true")
    parser.add_argument("--port", type=int, default=8050)
    args = parser.parse_args()

    load_dotenv()
    cfg = load_config(args.config)
    configure_logging()
    try:
        mode = validate_runtime_mode(cfg)
        proxy_configuration()
    except SafetyHalt as exc:
        log.critical("SAFETY HALT: %s", exc)
        return 2
    log.info("Bot City starting in %s", mode)

    stop_event = threading.Event()
    install_signal_handlers(stop_event)
    lock = SingleInstanceLock()
    lease = None
    durable = None
    clean_shutdown = False
    ledger = None
    broker = None
    workers = []

    try:
        lock.acquire()
        lease = configured_lease(stop_event)
        if lease:
            lease.acquire()
            lease.start()
        if cfg.broker == "webull":
            broker = WebullBroker(
                paper=cfg.paper,
                limit_buffer=cfg.limit_buffer,
                fill_timeout=cfg.fill_timeout,
            )
        else:
            broker = AlpacaBroker(paper=cfg.paper, fill_timeout=cfg.fill_timeout,
                                  ownership_guard=lease.assert_owned if lease else None)

        durable = configured_state(lease, broker)
        broker.journal = durable
        durable.start_session(broker)
        ledger = DurableLedger(durable)
        workers = [Worker(worker_cfg, cfg, broker, ledger) for worker_cfg in cfg.workers]
        ledger.attach(workers)
        ledger.write_health("starting", "startup reconciliation")
        restore(workers, ledger, broker)

        if not args.no_dashboard:
            serve_in_background(args.port)
            log.info("Dashboard: http://localhost:%d", args.port)

        ledger.write_health("running", "startup reconciliation complete")
        if os.getenv("BOT_CITY_TRADING_ENABLED", "false") == "true":
            run_loop(cfg, broker, ledger, workers, stop_event, lease=lease)
        elif any(worker.position for worker in workers):
            raise SafetyHalt("validation-only startup requires a flat account")
        else:
            log.info("VALIDATION PASSED: ownership and reconciliation; trading remains disabled")

        if lease:
            lease.assert_owned()

        # A requested shutdown is fail-safe: flatten managed paper positions if the market is open.
        if any(worker.position for worker in workers):
            clock = broker.clock()
            now = broker.now()
            if clock.is_open:
                close_managed_positions(workers, now, "process shutdown")
            else:
                raise SafetyHalt(
                    "shutdown occurred after market close with a managed position still open"
                )
        durable.finish_session(broker)
        if ledger:
            ledger.write_health("stopping", "clean shutdown")
        clean_shutdown = True
        return 0

    except SafetyHalt as exc:
        log.critical("SAFETY HALT: %s", exc, exc_info=True)
        if durable:
            try:
                durable.halt(str(exc))
            except SafetyHalt:
                log.exception("could not persist recovery marker; prior durable state remains blocking")
        if ledger:
            ledger.write_health("safety_halt", str(exc))
        return 2
    finally:
        try:
            if ledger:
                ledger.close()
            if lease and clean_shutdown:
                lease.release()
        finally:
            if lease:
                lease.stop()
            lock.release()


if __name__ == "__main__":
    sys.exit(main())
