"""Synthetic M15 data for `python main.py demo` and the tests.

random_walk(): a driftless random walk — there is NO edge to find in it.
A trustworthy harness must report "no edge" here; if it ever shows a strategy
passing the gate on this data, the harness (not the strategy) is broken.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def random_walk(days: int = 730, start: str = "2024-01-01", price: float = 1000.0,
                vol_per_bar: float = 0.002, seed: int = 0, trend_per_bar: float = 0.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = days * 96
    ts = pd.date_range(start, periods=n, freq="15min")
    rets = rng.normal(trend_per_bar, vol_per_bar, n)
    close = price * np.exp(np.cumsum(rets))
    open_ = np.r_[price, close[:-1]]
    wick = np.abs(rng.normal(0, vol_per_bar / 2, n)) * close
    return pd.DataFrame({"bar_ts": ts, "open_price": open_, "high_price": np.maximum(open_, close) + wick,
                         "low_price": np.minimum(open_, close) - wick, "close_price": close, "volume": 1.0})
