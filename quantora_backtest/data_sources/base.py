"""Where M15 bars come from. Backtest only ever READS — it never writes to the DB."""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

BAR_COLUMNS = ["bar_ts", "open_price", "high_price", "low_price", "close_price", "volume"]


class BarSource(ABC):
    @abstractmethod
    def load_m15(self, symbol: str) -> pd.DataFrame:
        """All stored M15 bars for `symbol`: BAR_COLUMNS, naive-UTC bar_ts, oldest first."""

    @abstractmethod
    def inventory(self) -> pd.DataFrame:
        """One row per symbol: symbol, bars, first, last."""

    def describe(self) -> str:
        return type(self).__name__

    def close(self) -> None:
        pass

    def __enter__(self) -> "BarSource":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def tidy(df: pd.DataFrame) -> pd.DataFrame:
    df = df[BAR_COLUMNS].copy()
    df["bar_ts"] = pd.to_datetime(df["bar_ts"])
    for c in BAR_COLUMNS[1:]:
        df[c] = df[c].astype(float)
    return df.drop_duplicates("bar_ts").sort_values("bar_ts").reset_index(drop=True)
