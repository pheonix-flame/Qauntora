"""What Notify reads. It writes NOTHING to the database (spec Section 11 gives it no table);
what it has already sent is kept in a small local state file instead (see state.py).

Reads: qtx_signals, qtx_signal_outcomes, qtx_signal_state, qtx_ohlcv.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod

import pandas as pd

SIGNAL_COLS = ["signal_id", "symbol", "trade_mode", "tier", "direction", "score", "entry_price", "stop_loss",
               "take_profit", "snapshot"]
OUTCOME_COLS = ["outcome_id", "signal_id", "outcome", "r_multiple", "resolved_price", "resolved_at",
                "symbol", "trade_mode", "tier", "direction"]


def as_json(v) -> dict:
    """JSON column -> dict (Oracle 23ai may already return a dict; LOBs need .read())."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return {}
    if isinstance(v, dict):
        return v
    v = v.read() if hasattr(v, "read") else v
    return json.loads(v) if v else {}


class NotifyReader(ABC):
    @abstractmethod
    def max_signal_id(self) -> int: ...

    @abstractmethod
    def signals_after(self, signal_id: int, limit: int = 500) -> pd.DataFrame: ...

    @abstractmethod
    def max_outcome_id(self) -> int: ...

    @abstractmethod
    def outcomes_after(self, outcome_id: int, limit: int = 500) -> pd.DataFrame: ...

    @abstractmethod
    def feed_freshness(self) -> pd.DataFrame:
        """symbol, asset_class, last_bar_ts (newest stored M15 bar start, naive UTC)."""

    @abstractmethod
    def core_states(self) -> pd.DataFrame:
        """symbol, trade_mode, tier, updated_at (naive UTC)."""

    @abstractmethod
    def unlabelled_confirmed(self) -> pd.DataFrame:
        """Tier 3 signals with no outcome yet: signal_id, symbol, trade_mode, close_ts."""

    def describe(self) -> str:
        return type(self).__name__

    def close(self) -> None:
        pass

    def __enter__(self) -> "NotifyReader":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
