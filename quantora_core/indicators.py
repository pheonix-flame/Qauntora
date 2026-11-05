"""Quantora Core indicators — one per market dimension (spec Section 5).

| dimension          | indicator            |
|--------------------|----------------------|
| trend direction    | EMA fast vs slow     |
| trend strength     | ADX (filter only)    |
| momentum           | RSI *or* MACD        |
| volatility context | Bollinger Bands (%b) |
| volume (crypto)    | OBV slope            |

All causal: the value at row i uses rows <= i only. Pure pandas, no TA-Lib.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(s: pd.Series, period: int) -> pd.Series:
    return s.ewm(span=period, adjust=False, min_periods=period).mean()


def _wilder(s: pd.Series, period: int) -> pd.Series:
    return s.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def true_range(bars: pd.DataFrame) -> pd.Series:
    prev = bars["close_price"].shift(1)
    return pd.concat([bars["high_price"] - bars["low_price"], (bars["high_price"] - prev).abs(),
                      (bars["low_price"] - prev).abs()], axis=1).max(axis=1)


def atr(bars: pd.DataFrame, period: int = 14) -> pd.Series:
    return _wilder(true_range(bars), period)


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = _wilder(delta.clip(lower=0), period)
    loss = _wilder(-delta.clip(upper=0), period)
    rs = gain / loss
    out = 100 - 100 / (1 + rs)
    flat = np.where(gain > 0, 100.0, 50.0)  # no losses: 100 if it rose, 50 if nothing moved
    return out.where(loss != 0, flat).where(gain.notna())


def adx(bars: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Returns adx, plus_di, minus_di (Wilder)."""
    up = bars["high_price"].diff()
    down = -bars["low_price"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=bars.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=bars.index)
    tr = _wilder(true_range(bars), period)
    plus_di = 100 * _wilder(plus_dm, period) / tr
    minus_di = 100 * _wilder(minus_dm, period) / tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return pd.DataFrame({"adx": _wilder(dx, period), "plus_di": plus_di, "minus_di": minus_di})


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def bollinger(close: pd.Series, period: int = 20, std: float = 2.0) -> pd.DataFrame:
    mid = close.rolling(period).mean()
    dev = close.rolling(period).std(ddof=0)
    upper, lower = mid + std * dev, mid - std * dev
    width = (upper - lower).replace(0, np.nan)
    return pd.DataFrame({"mid": mid, "upper": upper, "lower": lower, "pct_b": (close - lower) / width})


def obv(bars: pd.DataFrame) -> pd.Series:
    direction = np.sign(bars["close_price"].diff()).fillna(0)
    return (direction * bars["volume"]).cumsum()
