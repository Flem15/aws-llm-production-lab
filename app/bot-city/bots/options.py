from __future__ import annotations
from dataclasses import dataclass
from datetime import date, timedelta
import math


@dataclass(frozen=True)
class Contract:
    symbol: str        # OCC symbol, e.g. QQQ261008C00610000
    underlying: str
    kind: str          # "C" or "P"
    strike: float
    expiry: date


def occ_symbol(root: str, expiry: date, kind: str, strike: float) -> str:
    return f"{root}{expiry:%y%m%d}{kind}{int(round(strike * 1000)):08d}"


def add_trading_days(d: date, n: int) -> date:
    """Weekdays only (doesn't know exchange holidays; live mode uses the real chain)."""
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def target_strike(price: float, kind: str, otm_steps: int, step: float = 1.0) -> float:
    atm = round(price / step) * step
    return atm + otm_steps * step if kind == "C" else atm - otm_steps * step


def _ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_price(S: float, K: float, T_years: float, sigma: float, kind: str, r: float = 0.04) -> float:
    """Black-Scholes price; used only by the backtester to simulate option prices."""
    intrinsic = max(0.0, S - K) if kind == "C" else max(0.0, K - S)
    if T_years <= 0 or sigma <= 0:
        return intrinsic
    sq = sigma * math.sqrt(T_years)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T_years) / sq
    d2 = d1 - sq
    if kind == "C":
        return S * _ncdf(d1) - K * math.exp(-r * T_years) * _ncdf(d2)
    return K * math.exp(-r * T_years) * _ncdf(-d2) - S * _ncdf(-d1)
