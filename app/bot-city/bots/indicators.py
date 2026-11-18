"""Causal indicators on 1-minute bars (index = tz-aware US/Eastern timestamps).
Every value at row i only uses rows <= i, so the backtest can't peek at the future."""
import numpy as np
import pandas as pd


def vwap(df: pd.DataFrame) -> pd.Series:
    """Session VWAP, resets every trading day."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    day = df.index.date
    pv = (tp * df["volume"]).groupby(day).cumsum()
    vol = df["volume"].groupby(day).cumsum().replace(0, np.nan)
    return (pv / vol).fillna(df["close"])


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    prev = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / length, adjust=False).mean()


def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()
