"""Interface every storage backend implements.

Feed is the only writer of `qtx_ohlcv` and `qtx_news_events`. Writes are
insert-only and idempotent: re-running a backfill never edits an existing
row, so `loaded_at` stays the true first-insert time (audit rule, Section 11).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

import pandas as pd

from models import NewsEvent


class Store(ABC):
    @abstractmethod
    def latest_bar_ts(self, symbol: str, timeframe: str) -> datetime | None: ...

    @abstractmethod
    def insert_ohlcv(self, df: pd.DataFrame) -> int:
        """Insert bars not already stored. Returns how many were new."""

    @abstractmethod
    def insert_news(self, events: list[NewsEvent]) -> int: ...

    def close(self) -> None:
        pass

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
