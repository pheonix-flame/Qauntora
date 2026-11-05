"""Roll stored M15 bars up to the mode's timeframe.

Alignment decision (spec Section 12 asks for one to be picked and documented):
* H1 / H4 / D1 buckets are aligned to **UTC** (H4 = 00,04,08,... UTC; D1 = UTC midnight).
* W1 starts **Monday 00:00 UTC**.
Consequence: forex reopening Sunday ~22:00 UTC produces a short Sunday D1 bar.
That's accepted and consistent; Core must use the same rule.

`close_ts` is when a bar is final (start + length). A strategy may only act at
close_ts — never on a bar that hasn't closed (no look-ahead).
"""
from __future__ import annotations

import pandas as pd

from config import TIMEFRAME_MINUTES

_RULES = {"M15": "15min", "H1": "1h", "H4": "4h", "D1": "1D", "W1": "W-MON"}


def rollup(m15: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """M15 → timeframe. Returns bar_ts, close_ts, OHLC, volume, bars (M15 count)."""
    if m15.empty:
        return pd.DataFrame(columns=["bar_ts", "close_ts", "open_price", "high_price", "low_price",
                                     "close_price", "volume", "bars"])
    df = m15.set_index("bar_ts")
    if timeframe == "M15":
        out = df.assign(bars=1).reset_index()
    else:
        kw = {"label": "left", "closed": "left"}
        if timeframe == "W1":
            # pandas "W-MON" anchors on Mondays; label/closed left gives Monday-start weeks
            grouped = df.resample(_RULES[timeframe], **kw)
        elif timeframe in ("H1", "H4"):
            grouped = df.resample(_RULES[timeframe], origin="epoch", **kw)  # 00,04,08... UTC
        else:  # D1: day bins already start at UTC midnight
            grouped = df.resample(_RULES[timeframe], **kw)
        out = grouped.agg({"open_price": "first", "high_price": "max", "low_price": "min",
                           "close_price": "last", "volume": "sum"})
        out["bars"] = grouped["close_price"].count()
        out = out[out["bars"] > 0].reset_index()
    out["close_ts"] = out["bar_ts"] + pd.Timedelta(minutes=TIMEFRAME_MINUTES[timeframe])
    cols = ["bar_ts", "close_ts", "open_price", "high_price", "low_price", "close_price", "volume", "bars"]
    return out[cols].reset_index(drop=True)
