"""Oracle backend (current target), via python-oracledb in thin mode.

Thin mode is pure Python: no Oracle Instant Client to install. Works with a
local Oracle AI Database Free install (localhost:1521/FREEPDB1) or an Oracle
Cloud Autonomous Database (pass the wallet dir for mTLS, or use a TLS DSN).

Writes are MERGE ... WHEN NOT MATCHED THEN INSERT only: existing rows are
never updated, so `loaded_at` stays the true first-insert time.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from config import DBConfig
from models import NewsEvent
from resilience import with_retry
from storage.base import Store

log = logging.getLogger("quantora.feed")

BATCH_SIZE = 5_000


def split_sql_script(sql: str) -> list[str]:
    """Split a plain DDL script into statements for cursor.execute().

    Oracle drivers run one statement per call and reject a trailing ';'.
    Handles `--` line comments; not meant for PL/SQL blocks (the schema has none).
    """
    lines = []
    for line in sql.splitlines():
        # strip `--` comments that aren't inside a quoted literal
        out, in_quote = [], False
        i = 0
        while i < len(line):
            ch = line[i]
            if ch == "'":
                in_quote = not in_quote
            elif not in_quote and line.startswith("--", i):
                break
            out.append(ch)
            i += 1
        lines.append("".join(out))
    statements = [re.sub(r"\s+", " ", stmt).strip() for stmt in "\n".join(lines).split(";")]
    return [s for s in statements if s]


def ohlcv_rows(df: pd.DataFrame) -> list[tuple]:
    return [
        (r.symbol, r.asset_class, r.timeframe, pd.Timestamp(r.bar_ts).to_pydatetime(),
         float(r.open_price), float(r.high_price), float(r.low_price), float(r.close_price),
         None if pd.isna(r.volume) else float(r.volume), r.price_side)
        for r in df.itertuples(index=False)
    ]


class OracleStore(Store):
    _MERGE_OHLCV = """
        MERGE INTO qtx_ohlcv t
        USING (SELECT :1 AS symbol, :2 AS asset_class, :3 AS timeframe, :4 AS bar_ts,
                      :5 AS open_price, :6 AS high_price, :7 AS low_price, :8 AS close_price,
                      :9 AS volume, :10 AS price_side
               FROM dual) s
        ON (t.symbol = s.symbol AND t.timeframe = s.timeframe AND t.bar_ts = s.bar_ts)
        WHEN NOT MATCHED THEN INSERT
            (symbol, asset_class, timeframe, bar_ts, open_price, high_price,
             low_price, close_price, volume, price_side)
        VALUES (s.symbol, s.asset_class, s.timeframe, s.bar_ts, s.open_price, s.high_price,
                s.low_price, s.close_price, s.volume, s.price_side)"""
    _MERGE_NEWS = """
        MERGE INTO qtx_news_events t
        USING (SELECT :1 AS event_ts, :2 AS currency, :3 AS impact, :4 AS title FROM dual) s
        ON (t.event_ts = s.event_ts AND t.currency = s.currency AND t.title = s.title)
        WHEN NOT MATCHED THEN INSERT (event_ts, currency, impact, title)
        VALUES (s.event_ts, s.currency, s.impact, s.title)"""

    def __init__(self, cfg: DBConfig, driver=None) -> None:
        if driver is None:
            try:
                import oracledb  # type: ignore[import-not-found]
            except ImportError as exc:
                raise RuntimeError("python-oracledb is not installed (pip install oracledb).") from exc
            driver = oracledb
        if not cfg.password:
            raise ValueError("ORACLE_PASSWORD is empty — set it in quantora_feed/.env")
        self._db = driver
        params = {"user": cfg.user, "password": cfg.password, "dsn": cfg.dsn}
        if cfg.wallet_dir:
            params.update(config_dir=cfg.wallet_dir, wallet_location=cfg.wallet_dir,
                          wallet_password=cfg.wallet_password or None)
        self.conn = with_retry(lambda: driver.connect(**params), "Oracle connect")
        self._dsn = cfg.dsn

    def close(self) -> None:
        self.conn.close()

    # -- admin ----------------------------------------------------------------
    def describe(self) -> str:
        return f"Oracle Database {self.conn.version} at {self._dsn}"

    def schema_exists(self) -> bool:
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM user_tables WHERE table_name = 'QTX_OHLCV'")
        return cur.fetchone()[0] > 0

    def init_schema(self, schema_file: Path) -> bool:
        if self.schema_exists():
            return False
        cur = self.conn.cursor()
        statements = split_sql_script(schema_file.read_text(encoding="utf-8"))
        for stmt in statements:  # DDL auto-commits in Oracle
            log.debug("DDL: %s", stmt[:80])
            cur.execute(stmt)
        log.info("Ran %d DDL statements", len(statements))
        return True

    # -- data -----------------------------------------------------------------
    def latest_bar_ts(self, symbol: str, timeframe: str) -> datetime | None:
        cur = self.conn.cursor()
        cur.execute("SELECT MAX(bar_ts) FROM qtx_ohlcv WHERE symbol = :1 AND timeframe = :2",
                    [symbol, timeframe])
        row = cur.fetchone()
        return row[0] if row and row[0] is not None else None

    def insert_ohlcv(self, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        rows = ohlcv_rows(df)
        cur = self.conn.cursor()
        inserted = 0
        try:
            for i in range(0, len(rows), BATCH_SIZE):
                d = self._db
                # Explicit types: TIMESTAMP (not DATE) for bar_ts, and a stable type for
                # volume even when the first row's value is NULL.
                cur.setinputsizes(None, None, None, d.DB_TYPE_TIMESTAMP, d.DB_TYPE_BINARY_DOUBLE,
                                  d.DB_TYPE_BINARY_DOUBLE, d.DB_TYPE_BINARY_DOUBLE,
                                  d.DB_TYPE_BINARY_DOUBLE, d.DB_TYPE_BINARY_DOUBLE, None)
                cur.executemany(self._MERGE_OHLCV, rows[i:i + BATCH_SIZE])
                inserted += max(cur.rowcount, 0)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return inserted

    def insert_news(self, events: list[NewsEvent]) -> int:
        if not events:
            return 0
        cur = self.conn.cursor()
        try:
            cur.setinputsizes(self._db.DB_TYPE_TIMESTAMP, None, None, None)
            cur.executemany(self._MERGE_NEWS, [(e.event_ts, e.currency, e.impact, e.title) for e in events])
            inserted = max(cur.rowcount, 0)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return inserted
