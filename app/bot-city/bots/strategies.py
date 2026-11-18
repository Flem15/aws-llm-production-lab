"""Entry signals. A strategy turns 1-min bars into CALL / PUT / nothing.

  vwap_cross  ("QQQ OG" in the videos): price closes back across session VWAP,
              and is not already stretched too far from it (measured in ATRs).
  trend         fast EMA crosses slow EMA, on the same side of VWAP.
  ema_pullback  ("QQQ TREND" in the 3rd video): in a trend, wait for price to pull back
                to EMA9 ("setup forming: PUT at EMA9, 0.12 ATR"), then fire when it
                bounces away in the trend direction. If the trend breaks or the bounce
                never comes, the setup is dropped ("setup faded, standing down").
"""
from __future__ import annotations
from dataclasses import dataclass
import pandas as pd
from . import indicators as ind


@dataclass
class Signal:
    side: str      # "CALL" or "PUT"
    reason: str    # human-readable "thought" for the log / dashboard


class Strategy:
    name = "base"
    warmup = 20

    def __init__(self, **params):
        self.params = params
        self.note: str | None = None     # optional "thought" for the dashboard, read+cleared by the worker

    def prepare(self, bars: pd.DataFrame) -> pd.DataFrame:
        raise NotImplementedError

    def signal(self, frame: pd.DataFrame) -> Signal | None:
        raise NotImplementedError

    @staticmethod
    def _today_rows(frame: pd.DataFrame) -> int:
        return int((frame.index.date == frame.index[-1].date()).sum())


class VwapCross(Strategy):
    name = "vwap_cross"

    def prepare(self, bars):
        df = bars.copy()
        df["vwap"] = ind.vwap(df)
        df["atr"] = ind.atr(df, self.params.get("atr_length", 14))
        return df

    def signal(self, frame):
        if len(frame) < self.warmup or self._today_rows(frame) < 3:
            return None
        prev, cur = frame.iloc[-2], frame.iloc[-1]
        if cur["atr"] <= 0:
            return None
        dist = (cur["close"] - cur["vwap"]) / cur["atr"]
        max_d = self.params.get("max_atr_distance", 0.8)
        if prev["close"] <= prev["vwap"] < cur["close"] and dist <= max_d:
            return Signal("CALL", f"VWAP cross ▲ CALL · {dist:.2f} ATR away")
        if prev["close"] >= prev["vwap"] > cur["close"] and -dist <= max_d:
            return Signal("PUT", f"VWAP cross ▼ PUT · {-dist:.2f} ATR away")
        return None


class Trend(Strategy):
    name = "trend"

    def prepare(self, bars):
        df = bars.copy()
        df["vwap"] = ind.vwap(df)
        df["ema_fast"] = ind.ema(df["close"], self.params.get("fast", 9))
        df["ema_slow"] = ind.ema(df["close"], self.params.get("slow", 21))
        return df

    def signal(self, frame):
        if len(frame) < self.warmup or self._today_rows(frame) < 5:
            return None
        prev, cur = frame.iloc[-2], frame.iloc[-1]
        up = prev["ema_fast"] <= prev["ema_slow"] and cur["ema_fast"] > cur["ema_slow"]
        dn = prev["ema_fast"] >= prev["ema_slow"] and cur["ema_fast"] < cur["ema_slow"]
        if up and cur["close"] > cur["vwap"]:
            return Signal("CALL", "Trend ▲ EMA cross above VWAP")
        if dn and cur["close"] < cur["vwap"]:
            return Signal("PUT", "Trend ▼ EMA cross below VWAP")
        return None


class EmaPullback(Strategy):
    name = "ema_pullback"

    def __init__(self, **params):
        super().__init__(**params)
        self.setup = None        # (side, bars_waited, day)
        self._scan_n = 0

    def prepare(self, bars):
        df = bars.copy()
        df["vwap"] = ind.vwap(df)
        df["atr"] = ind.atr(df, self.params.get("atr_length", 14))
        df["ema_fast"] = ind.ema(df["close"], self.params.get("fast", 9))
        df["ema_slow"] = ind.ema(df["close"], self.params.get("slow", 21))
        return df

    def signal(self, frame):
        if len(frame) < self.warmup or self._today_rows(frame) < 10:
            return None
        prev, cur = frame.iloc[-2], frame.iloc[-1]
        if cur["atr"] <= 0:
            return None
        day = frame.index[-1].date()
        sym = self.params.get("symbol", "")
        touch = self.params.get("touch_atr", 0.25)        # "at EMA9" = within this many ATRs
        confirm = self.params.get("confirm_atr", 0.10)    # bounce needed to fire
        max_wait = self.params.get("max_wait_bars", 8)
        up = cur["ema_fast"] > cur["ema_slow"] and cur["close"] > cur["vwap"]
        dn = cur["ema_fast"] < cur["ema_slow"] and cur["close"] < cur["vwap"]
        dist = (cur["close"] - cur["ema_fast"]) / cur["atr"]      # + above EMA9, − below

        if self.setup and self.setup[2] != day:
            self.setup = None
        if self.setup is None:
            if (up or dn) and abs(dist) <= touch:
                side = "CALL" if up else "PUT"
                self.setup = (side, 0, day)
                self.note = f"setup forming: {'▲' if up else '▼'} {side} at EMA9, {abs(dist):.2f} ATR"
            else:
                self._scan_n += 1
                if self._scan_n % 15 == 1:
                    self.note = f"scanning {sym} · EMA9 {abs(dist):.2f} ATR away"
            return None

        side, waited, _ = self.setup
        waited += 1
        trend_ok = up if side == "CALL" else dn
        if not trend_ok or waited > max_wait:
            self.setup = None
            self.note = "setup faded, standing down"
            return None
        bounced = (dist >= confirm and cur["close"] > prev["close"]) if side == "CALL" \
            else (dist <= -confirm and cur["close"] < prev["close"])
        if bounced:
            self.setup = None
            arrow = "▲" if side == "CALL" else "▼"
            return Signal(side, f"EMA9 pullback {arrow} {side} · bounced {abs(dist):.2f} ATR")
        self.setup = (side, waited, day)
        return None


STRATEGIES = {c.name: c for c in (VwapCross, Trend, EmaPullback)}


def make_strategy(name: str, params: dict, symbol: str = "") -> Strategy:
    if name not in STRATEGIES:
        raise ValueError(f"unknown strategy '{name}'. Options: {list(STRATEGIES)}")
    return STRATEGIES[name](**{"symbol": symbol, **params})
