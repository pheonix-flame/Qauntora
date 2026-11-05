"""--dry-run backend: CSVs in preview_output/, database untouched."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import pandas as pd

from models import NewsEvent, empty_ohlcv
from storage.base import Store


class PreviewStore(Store):
    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        out_dir.mkdir(parents=True, exist_ok=True)

    def _ohlcv_path(self, symbol: str, timeframe: str) -> Path:
        return self.out_dir / f"ohlcv_{symbol}_{timeframe}.csv"

    def latest_bar_ts(self, symbol: str, timeframe: str) -> datetime | None:
        path = self._ohlcv_path(symbol, timeframe)
        if not path.exists():
            return None
        existing = pd.read_csv(path, usecols=["bar_ts"], parse_dates=["bar_ts"])
        return None if existing.empty else existing["bar_ts"].max().to_pydatetime()

    def load_bars(self, symbol: str, timeframe: str, start: datetime | None = None) -> pd.DataFrame:
        path = self._ohlcv_path(symbol, timeframe)
        if not path.exists():
            return empty_ohlcv()
        df = pd.read_csv(path, parse_dates=["bar_ts"])
        if start is not None:
            df = df[df["bar_ts"] >= pd.Timestamp(start)]
        return df.sort_values("bar_ts").reset_index(drop=True)

    def insert_ohlcv(self, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        path = self._ohlcv_path(df["symbol"].iloc[0], df["timeframe"].iloc[0])
        return self._append_unique(path, df, ["symbol", "timeframe", "bar_ts"], "bar_ts")

    def insert_news(self, events: list[NewsEvent]) -> int:
        if not events:
            return 0
        new = pd.DataFrame([asdict(e) for e in events])
        return self._append_unique(self.out_dir / "news_events.csv", new,
                                   ["event_ts", "currency", "title"], "event_ts")

    @staticmethod
    def _append_unique(path: Path, new: pd.DataFrame, key: list[str], ts_col: str) -> int:
        if path.exists():
            old = pd.read_csv(path, parse_dates=[ts_col])
            merged = pd.concat([old, new], ignore_index=True).drop_duplicates(subset=key, keep="first")
            added = len(merged) - len(old)
        else:
            merged, added = new.drop_duplicates(subset=key), len(new.drop_duplicates(subset=key))
        merged.sort_values(ts_col).to_csv(path, index=False)
        return added
