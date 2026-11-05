"""Read Feed's --dry-run CSVs (ohlcv_<SYMBOL>_M15.csv) — lets you backtest without Oracle."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from data_sources.base import BarSource, tidy


class CsvBarSource(BarSource):
    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)
        if not self.folder.is_dir():
            raise FileNotFoundError(f"CSV folder not found: {self.folder}")

    def describe(self) -> str:
        return f"CSV files in {self.folder}"

    def _path(self, symbol: str) -> Path:
        return self.folder / f"ohlcv_{symbol}_M15.csv"

    def load_m15(self, symbol: str) -> pd.DataFrame:
        path = self._path(symbol)
        if not path.exists():
            raise LookupError(f"No {path.name} in {self.folder}")
        return tidy(pd.read_csv(path))

    def inventory(self) -> pd.DataFrame:
        rows = []
        for path in sorted(self.folder.glob("ohlcv_*_M15.csv")):
            df = pd.read_csv(path, usecols=["bar_ts"], parse_dates=["bar_ts"])
            rows.append((path.stem[len("ohlcv_"):-len("_M15")], len(df), df["bar_ts"].min(), df["bar_ts"].max()))
        return pd.DataFrame(rows, columns=["symbol", "bars", "first", "last"])
