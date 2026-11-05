"""Minimal, causal indicators the harness itself needs (ATR for barriers, EMA
for the reference strategy). Quantora Core will own the full indicator set
(spec Section 5); this engine doesn't import it.

Every value at row i uses only rows <= i.
"""
from __future__ import annotations

import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def atr(bars: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder's Average True Range."""
    prev_close = bars["close_price"].shift(1)
    tr = pd.concat([bars["high_price"] - bars["low_price"],
                    (bars["high_price"] - prev_close).abs(),
                    (bars["low_price"] - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
