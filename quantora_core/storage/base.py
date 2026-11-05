"""What Core reads and writes (spec Section 11 ownership).

Reads:  qtx_ohlcv, qtx_news_events, qtx_config_versions (approved rows only)
Writes: qtx_signal_state (one row per symbol/mode, always current)
        qtx_signals      (append-only log of every tier change)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

import pandas as pd

from config import SignalConfig

BAR_COLUMNS = ["bar_ts", "open_price", "high_price", "low_price", "close_price", "volume"]


class CoreStore(ABC):
    # -- reads ---------------------------------------------------------------
    @abstractmethod
    def load_m15(self, symbol: str, since: datetime | None = None) -> pd.DataFrame: ...

    @abstractmethod
    def news_events(self, start: datetime, end: datetime) -> pd.DataFrame:
        """event_ts, currency, impact, title."""

    @abstractmethod
    def approved_config(self, symbol: str, mode: str, base: SignalConfig) -> SignalConfig | None:
        """Latest approved qtx_config_versions row merged over `base`, or None."""

    @abstractmethod
    def get_state(self, symbol: str, mode: str) -> dict | None:
        """{tier, direction, score, snapshot(dict), notes} or None."""

    @abstractmethod
    def list_states(self) -> pd.DataFrame: ...

    # -- writes ----------------------------------------------------------------
    @abstractmethod
    def save_state(self, symbol: str, mode: str, tier: str, direction: str | None, score: float,
                   snapshot: dict, notes: str) -> None: ...

    @abstractmethod
    def append_signal(self, symbol: str, mode: str, tier: str, direction: str | None, score: float,
                      entry: float | None, stop: float | None, target: float | None, snapshot: dict) -> int: ...

    def commit(self) -> None:
        pass

    def describe(self) -> str:
        return type(self).__name__

    def close(self) -> None:
        pass

    def __enter__(self) -> "CoreStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def tidy_bars(df: pd.DataFrame) -> pd.DataFrame:
    df = df[BAR_COLUMNS].copy()
    df["bar_ts"] = pd.to_datetime(df["bar_ts"])
    for c in BAR_COLUMNS[1:]:
        df[c] = df[c].astype(float)
    return df.drop_duplicates("bar_ts").sort_values("bar_ts").reset_index(drop=True)
