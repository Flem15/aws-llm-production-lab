"""Loading 1-minute stock bars for backtests: from CSV, from Alpaca, or synthetic demo data."""
from __future__ import annotations
import os
import zlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd

ET = ZoneInfo("America/New_York")


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    df.index = pd.to_datetime(df.index)
    df.index = df.index.tz_localize("UTC").tz_convert(ET) if df.index.tz is None else df.index.tz_convert(ET)
    df = df.between_time("09:30", "15:59")
    return df[["open", "high", "low", "close", "volume"]].sort_index()


def load_csv(path: str) -> pd.DataFrame:
    """CSV with a timestamp column (UTC or tz-aware) + open,high,low,close,volume."""
    df = pd.read_csv(path)
    ts = next(c for c in df.columns if c.lower() in ("timestamp", "time", "datetime", "date"))
    df = df.rename(columns=str.lower).set_index(ts.lower())
    return _clean(df)


def load_alpaca(symbol: str, days: int, end: datetime | None = None) -> pd.DataFrame:
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.data.enums import DataFeed
    c = StockHistoricalDataClient(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"])
    end = end or (datetime.now(ET) - timedelta(minutes=16))   # free plan: no SIP data from the last 15 min
    feed = DataFeed.SIP if os.getenv("ALPACA_DATA_FEED", "iex").lower() == "sip" else DataFeed.IEX
    req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Minute,
                           start=end - timedelta(days=int(days * 7 / 5) + 3), end=end, feed=feed)
    df = c.get_stock_bars(req).df
    df = df.xs(symbol, level=0) if isinstance(df.index, pd.MultiIndex) else df
    df = _clean(df)
    keep = sorted(set(df.index.date))[-days:]
    return df[np.isin(df.index.date, keep)]


def synthetic(symbol: str, days: int, seed: int = 7) -> pd.DataFrame:
    """FAKE random-walk data so you can test the plumbing with no API keys.
    Results on this data mean nothing about real performance."""
    start_px = {"QQQ": 600.0, "SPY": 660.0, "IWM": 240.0}.get(symbol, 100.0)
    rng = np.random.default_rng(zlib.crc32(f'{symbol}-{seed}'.encode()))
    frames, px = [], start_px
    day = pd.Timestamp("2026-09-01", tz=ET)
    while len(frames) < days:
        if day.weekday() < 5:
            idx = pd.date_range(day + pd.Timedelta(hours=9, minutes=30), periods=390, freq="1min")
            drift = rng.normal(0, 0.00002)                      # ~0.8%/day trend
            rets = rng.normal(drift, 0.0006, 390)              # pure random walk: no real edge exists
            close = px * np.exp(np.cumsum(rets))
            open_ = np.r_[px, close[:-1]]
            spread = np.abs(rng.normal(0, 0.0004, 390)) * close
            frames.append(pd.DataFrame({"open": open_, "high": np.maximum(open_, close) + spread,
                                        "low": np.minimum(open_, close) - spread, "close": close,
                                        "volume": rng.integers(50_000, 400_000, 390)}, index=idx))
            px = close[-1]
        day += pd.Timedelta(days=1)
    return pd.concat(frames)
