from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import os

from .errors import SafetyHalt


@dataclass(frozen=True)
class SessionSchedule:
    first_entry: datetime
    last_entry: datetime
    flatten: datetime
    market_close: datetime


def session_schedule(now: datetime, market_close: datetime, cfg) -> SessionSchedule:
    """Build today's schedule, automatically moving risk cutoffs earlier on early-close sessions."""
    if market_close.tzinfo is None:
        market_close = market_close.replace(tzinfo=now.tzinfo)
    else:
        market_close = market_close.astimezone(now.tzinfo)

    configured_first = now.replace(
        hour=cfg.first_entry.hour,
        minute=cfg.first_entry.minute,
        second=0,
        microsecond=0,
    )
    configured_last = now.replace(
        hour=cfg.last_entry.hour,
        minute=cfg.last_entry.minute,
        second=0,
        microsecond=0,
    )
    configured_flatten = now.replace(
        hour=cfg.flatten.hour,
        minute=cfg.flatten.minute,
        second=0,
        microsecond=0,
    )

    # Preserve the normal 15:50 flatten, but on an early close flatten 10 minutes before the actual close.
    flatten = min(configured_flatten, market_close - timedelta(minutes=10))
    # Preserve the normal 15:30 last entry, but always leave at least 20 minutes before flattening.
    last_entry = min(configured_last, flatten - timedelta(minutes=20))

    if not configured_first < last_entry < flatten < market_close:
        raise SafetyHalt(
            f"invalid session schedule: first={configured_first.isoformat()} "
            f"last={last_entry.isoformat()} flatten={flatten.isoformat()} "
            f"close={market_close.isoformat()}"
        )

    return SessionSchedule(
        first_entry=configured_first,
        last_entry=last_entry,
        flatten=flatten,
        market_close=market_close,
    )


def validate_runtime_mode(cfg) -> str:
    """Day 15 is deliberately Alpaca-paper-only. Live and Webull execution are disabled."""
    mode = os.getenv("BOT_CITY_MODE", "paper-alpaca").strip().lower()

    if os.getenv("ALLOW_LIVE_TRADING"):
        raise SafetyHalt("ALLOW_LIVE_TRADING must not be set in the Day 15 paper-only build")

    if mode != "paper-alpaca":
        raise SafetyHalt("Day 15 permits only BOT_CITY_MODE=paper-alpaca")

    if not cfg.paper:
        raise SafetyHalt("Day 15 paper-only build requires account.paper: true")

    if cfg.broker != "alpaca":
        raise SafetyHalt("Day 15 paper-only build requires account.broker: alpaca")

    if os.getenv("WEBULL_ENDPOINT") or os.getenv("WEBULL_APP_KEY") or os.getenv("WEBULL_APP_SECRET"):
        raise SafetyHalt("Webull credentials/endpoints must not be present in the Day 15 Alpaca-only runtime")

    return mode
