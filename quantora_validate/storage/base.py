"""What Validate reads and writes (spec Section 11 ownership).

Reads:  qtx_ohlcv, qtx_signals          Writes: qtx_signal_outcomes (insert-only)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

import pandas as pd

BAR_COLUMNS = ["bar_ts", "open_price", "high_price", "low_price", "close_price"]
SIGNAL_COLUMNS = ["signal_id", "symbol", "trade_mode", "tier", "direction", "entry_price", "stop_loss",
                  "take_profit", "snapshot", "created_at"]
OUTCOME_COLUMNS = ["signal_id", "outcome", "r_multiple", "resolved_price", "resolved_at"]


class ValidateStore(ABC):
    @abstractmethod
    def unlabelled_signals(self) -> pd.DataFrame:
        """CONFIRMED/FORMING signals with a direction and no outcome row yet (SIGNAL_COLUMNS; snapshot = dict)."""

    @abstractmethod
    def load_m15(self, symbol: str, start: datetime, end: datetime) -> tuple[pd.DataFrame, str]:
        """(bars, asset_class) for symbol between start and end."""

    @abstractmethod
    def insert_outcomes(self, rows: list[dict]) -> int: ...

    @abstractmethod
    def outcomes_report(self) -> pd.DataFrame:
        """Outcomes joined to their signals: symbol, trade_mode, tier, outcome, r_multiple, resolved_at."""

    def describe(self) -> str:
        return type(self).__name__

    def close(self) -> None:
        pass

    def __enter__(self) -> "ValidateStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
