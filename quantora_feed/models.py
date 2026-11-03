"""Quantora Feed — data shapes shared by every module inside Feed.

These mirror the `qtx_ohlcv` / `qtx_news_events` columns. Sources produce
them, stores consume them; neither needs to know about the other.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

BAR_MINUTES = {"M15": 15}

OHLCV_COLUMNS = [
    "symbol", "asset_class", "timeframe", "bar_ts",
    "open_price", "high_price", "low_price", "close_price",
    "volume", "price_side",
]


def empty_ohlcv() -> pd.DataFrame:
    return pd.DataFrame(columns=OHLCV_COLUMNS)


def naive_utc(ts: datetime) -> pd.Timestamp:
    """Aware -> converted to UTC and stripped; naive -> assumed already UTC."""
    t = pd.Timestamp(ts)
    return t.tz_convert("UTC").tz_localize(None) if t.tzinfo else t


@dataclass(frozen=True)
class NewsEvent:
    event_ts: datetime  # naive UTC
    currency: str
    impact: str          # LOW | MEDIUM | HIGH
    title: str
