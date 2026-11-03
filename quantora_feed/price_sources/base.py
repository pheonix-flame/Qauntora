"""Interface every price source implements (MT5 today, ccxt later)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

import pandas as pd

from config import FeedConfig, SymbolSpec


class PriceSource(ABC):
    #: registry key, matched against SymbolSpec.source
    name: str = ""
    connected: bool = False

    @classmethod
    @abstractmethod
    def from_config(cls, cfg: FeedConfig) -> "PriceSource": ...

    @abstractmethod
    def connect(self, symbols: list[SymbolSpec]) -> None:
        """Open the connection. `symbols` are the ones this source will serve."""

    @abstractmethod
    def shutdown(self) -> None: ...

    @abstractmethod
    def fetch_bars(self, spec: SymbolSpec, start_utc: datetime, timeframe: str = "M15") -> pd.DataFrame:
        """All *closed* bars from start_utc to now, as qtx_ohlcv-shaped rows in true UTC.

        Raise LookupError if the symbol doesn't exist on this source.
        """

    def symbol_specs(self, spec: SymbolSpec) -> dict | None:
        """Contract details for `check` (min lot, contract size, ...). Optional."""
        return None

    def suggest_names(self, pattern: str) -> list[str]:
        return []

    def describe_clock(self) -> str:
        return "UTC"
