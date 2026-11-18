"""Builds the price-chart payload for the dashboard: today's 1-min closes with VWAP and EMA9."""
import pandas as pd
from . import indicators as ind


def chart_payload(bars: pd.DataFrame, day=None, ema_span: int = 9) -> dict | None:
    if bars is None or bars.empty:
        return None
    df = bars.copy()
    df["vwap"] = ind.vwap(df)
    df["ema9"] = ind.ema(df["close"], ema_span)          # computed on the full history, so it's warmed up
    day = day or df.index[-1].date()
    df = df[df.index.date == day]
    if df.empty:
        return None
    r = lambda s: [round(float(x), 2) for x in s]
    return {"day": str(day), "t": [ts.strftime("%H:%M") for ts in df.index],
            "close": r(df["close"]), "vwap": r(df["vwap"]), "ema9": r(df["ema9"])}
