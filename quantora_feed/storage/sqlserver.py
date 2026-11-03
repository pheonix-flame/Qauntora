"""SQL Server backend (current dev target). An Oracle backend would be a sibling module."""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

from models import NewsEvent
from resilience import with_retry
from storage.base import Store

log = logging.getLogger("quantora.feed")


def ohlcv_rows(df: pd.DataFrame) -> list[tuple]:
    return [
        (r.symbol, r.asset_class, r.timeframe, pd.Timestamp(r.bar_ts).to_pydatetime(),
         float(r.open_price), float(r.high_price), float(r.low_price), float(r.close_price),
         None if pd.isna(r.volume) else float(r.volume), r.price_side)
        for r in df.itertuples(index=False)
    ]


class SqlServerStore(Store):
    _STAGE_DDL = """
        IF OBJECT_ID('tempdb..#qtx_ohlcv_stage') IS NOT NULL DROP TABLE #qtx_ohlcv_stage;
        CREATE TABLE #qtx_ohlcv_stage (
            symbol VARCHAR(20), asset_class VARCHAR(20), timeframe VARCHAR(5), bar_ts DATETIME2,
            open_price DECIMAL(18,6), high_price DECIMAL(18,6), low_price DECIMAL(18,6),
            close_price DECIMAL(18,6), volume DECIMAL(18,2), price_side VARCHAR(4)
        );"""
    _STAGE_INSERT = "INSERT INTO #qtx_ohlcv_stage VALUES (?,?,?,?,?,?,?,?,?,?)"
    _INSERT_NEW = """
        INSERT INTO qtx_ohlcv (symbol, asset_class, timeframe, bar_ts, open_price, high_price,
                               low_price, close_price, volume, price_side)
        SELECT s.symbol, s.asset_class, s.timeframe, s.bar_ts, s.open_price, s.high_price,
               s.low_price, s.close_price, s.volume, s.price_side
        FROM #qtx_ohlcv_stage s
        WHERE NOT EXISTS (SELECT 1 FROM qtx_ohlcv t
                          WHERE t.symbol = s.symbol AND t.timeframe = s.timeframe AND t.bar_ts = s.bar_ts);"""
    _NEWS_INSERT = """
        INSERT INTO qtx_news_events (event_ts, currency, impact, title)
        SELECT ?, ?, ?, ?
        WHERE NOT EXISTS (SELECT 1 FROM qtx_news_events
                          WHERE event_ts = ? AND currency = ? AND title = ?);"""

    def __init__(self, conn_str: str, connect=None) -> None:
        if connect is None:
            try:
                import pyodbc  # type: ignore[import-not-found]
            except ImportError as exc:
                raise RuntimeError("pyodbc is not installed (pip install pyodbc) and needs "
                                   "Microsoft 'ODBC Driver 18 for SQL Server'.") from exc
            connect = pyodbc.connect
        self.conn = with_retry(lambda: connect(conn_str, autocommit=False), "SQL Server connect")

    def close(self) -> None:
        self.conn.close()

    def ping(self) -> str:
        cur = self.conn.cursor()
        cur.execute("SELECT @@VERSION")
        return cur.fetchone()[0].splitlines()[0]

    def table_exists(self, name: str) -> bool:
        cur = self.conn.cursor()
        cur.execute("SELECT 1 FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = ?", name)
        return cur.fetchone() is not None

    def init_schema(self, schema_file: Path) -> bool:
        """Create all qtx_ tables. Returns False (and does nothing) if they already exist."""
        if self.table_exists("qtx_ohlcv"):
            return False
        cur = self.conn.cursor()
        cur.execute(schema_file.read_text(encoding="utf-8"))
        while cur.nextset():
            pass
        self.conn.commit()
        return True

    def latest_bar_ts(self, symbol: str, timeframe: str) -> datetime | None:
        cur = self.conn.cursor()
        cur.execute("SELECT MAX(bar_ts) FROM qtx_ohlcv WHERE symbol = ? AND timeframe = ?", symbol, timeframe)
        row = cur.fetchone()
        return row[0] if row and row[0] is not None else None

    def insert_ohlcv(self, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        cur = self.conn.cursor()
        try:
            cur.execute(self._STAGE_DDL)
            cur.fast_executemany = True
            cur.executemany(self._STAGE_INSERT, ohlcv_rows(df))
            cur.execute(self._INSERT_NEW)
            inserted = cur.rowcount
            cur.execute("DROP TABLE #qtx_ohlcv_stage;")
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return max(inserted, 0)

    def insert_news(self, events: list[NewsEvent]) -> int:
        if not events:
            return 0
        cur = self.conn.cursor()
        inserted = 0
        try:
            for e in events:
                cur.execute(self._NEWS_INSERT, e.event_ts, e.currency, e.impact, e.title,
                            e.event_ts, e.currency, e.title)
                inserted += max(cur.rowcount, 0)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return inserted
