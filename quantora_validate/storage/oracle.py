"""Oracle backend for Validate (python-oracledb thin mode)."""
from __future__ import annotations

import json
from datetime import datetime

import pandas as pd

from config import DBConfig
from storage.base import BAR_COLUMNS, SIGNAL_COLUMNS, ValidateStore


def _json(v) -> dict:
    """JSON column value -> dict. Oracle 23ai+ may hand back an IS JSON CLOB already parsed."""
    if v is None:
        return {}
    if isinstance(v, (dict, list)):
        return v
    v = v.read() if hasattr(v, "read") else v
    return json.loads(v) if v else {}


class OracleValidateStore(ValidateStore):
    _INSERT = """
        INSERT INTO qtx_signal_outcomes (signal_id, outcome, r_multiple, resolved_price, resolved_at)
        SELECT :1, :2, :3, :4, :5 FROM dual
        WHERE NOT EXISTS (SELECT 1 FROM qtx_signal_outcomes WHERE signal_id = :6)"""

    def __init__(self, cfg: DBConfig, driver=None) -> None:
        if driver is None:
            try:
                import oracledb  # type: ignore[import-not-found]
            except ImportError as exc:
                raise RuntimeError("python-oracledb is not installed (pip install oracledb).") from exc
            driver = oracledb
        if not cfg.password:
            raise ValueError("ORACLE_PASSWORD is empty — set it in Qauntora/.env")
        self._db = driver
        params = {"user": cfg.user, "password": cfg.password, "dsn": cfg.dsn}
        if cfg.wallet_dir:
            params.update(config_dir=cfg.wallet_dir, wallet_location=cfg.wallet_dir,
                          wallet_password=cfg.wallet_password or None)
        self.conn = driver.connect(**params)
        self._dsn = cfg.dsn

    def describe(self) -> str:
        return f"Oracle {self.conn.version} at {self._dsn}"

    def close(self) -> None:
        self.conn.close()

    def unlabelled_signals(self) -> pd.DataFrame:
        cur = self.conn.cursor()
        cur.execute("""SELECT s.signal_id, s.symbol, s.trade_mode, s.tier, s.direction, s.entry_price,
                              s.stop_loss, s.take_profit, s.indicator_snapshot_json, s.created_at
                       FROM qtx_signals s
                       WHERE s.tier IN ('CONFIRMED', 'FORMING') AND s.direction IN ('BUY', 'SELL')
                         AND NOT EXISTS (SELECT 1 FROM qtx_signal_outcomes o WHERE o.signal_id = s.signal_id)
                       ORDER BY s.signal_id""")
        rows = [list(r[:8]) + [_json(r[8]), r[9]] for r in cur.fetchall()]
        return pd.DataFrame(rows, columns=SIGNAL_COLUMNS)

    def load_m15(self, symbol: str, start: datetime, end: datetime) -> tuple[pd.DataFrame, str]:
        cur = self.conn.cursor()
        cur.arraysize = 10_000
        cur.execute(f"SELECT {', '.join(BAR_COLUMNS)}, asset_class FROM qtx_ohlcv "
                    "WHERE symbol = :1 AND timeframe = 'M15' AND bar_ts >= :2 AND bar_ts <= :3 ORDER BY bar_ts",
                    [symbol, start, end])
        rows = cur.fetchall()
        df = pd.DataFrame([r[:5] for r in rows], columns=BAR_COLUMNS)
        df["bar_ts"] = pd.to_datetime(df["bar_ts"])
        return df, (rows[0][5] if rows else "SYNTHETIC")

    def insert_outcomes(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        cur = self.conn.cursor()
        cur.setinputsizes(None, None, None, None, self._db.DB_TYPE_TIMESTAMP, None)
        cur.executemany(self._INSERT, [(r["signal_id"], r["outcome"], r["r_multiple"], r["resolved_price"],
                                        pd.Timestamp(r["resolved_at"]).to_pydatetime(), r["signal_id"])
                                       for r in rows])
        n = max(cur.rowcount, 0)
        self.conn.commit()
        return n

    def outcomes_report(self) -> pd.DataFrame:
        cur = self.conn.cursor()
        cur.execute("""SELECT s.symbol, s.trade_mode, s.tier, o.outcome, o.r_multiple, o.resolved_at
                       FROM qtx_signal_outcomes o JOIN qtx_signals s ON s.signal_id = o.signal_id""")
        return pd.DataFrame(cur.fetchall(), columns=["symbol", "trade_mode", "tier", "outcome", "r_multiple",
                                                     "resolved_at"])
