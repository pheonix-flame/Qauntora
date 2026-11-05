"""Oracle reader for Notify (SELECTs only)."""
from __future__ import annotations

import pandas as pd

from config import DBConfig
from storage.base import OUTCOME_COLS, SIGNAL_COLS, NotifyReader, as_json


class OracleReader(NotifyReader):
    def __init__(self, cfg: DBConfig, driver=None) -> None:
        if driver is None:
            try:
                import oracledb  # type: ignore[import-not-found]
            except ImportError as exc:
                raise RuntimeError("python-oracledb is not installed (pip install oracledb).") from exc
            driver = oracledb
        if not cfg.password:
            raise ValueError("ORACLE_PASSWORD is empty — set it in Qauntora/.env")
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

    def _rows(self, sql: str, params=None) -> list:
        cur = self.conn.cursor()
        cur.execute(sql, params or [])
        return cur.fetchall()

    def max_signal_id(self) -> int:
        return int(self._rows("SELECT NVL(MAX(signal_id), 0) FROM qtx_signals")[0][0])

    def signals_after(self, signal_id: int, limit: int = 500) -> pd.DataFrame:
        rows = self._rows("""SELECT signal_id, symbol, trade_mode, tier, direction, score, entry_price, stop_loss,
                                    take_profit, indicator_snapshot_json
                             FROM qtx_signals WHERE signal_id > :1 ORDER BY signal_id
                             FETCH FIRST :2 ROWS ONLY""", [signal_id, limit])
        return pd.DataFrame([list(r[:9]) + [as_json(r[9])] for r in rows], columns=SIGNAL_COLS)

    def max_outcome_id(self) -> int:
        return int(self._rows("SELECT NVL(MAX(outcome_id), 0) FROM qtx_signal_outcomes")[0][0])

    def outcomes_after(self, outcome_id: int, limit: int = 500) -> pd.DataFrame:
        rows = self._rows("""SELECT o.outcome_id, o.signal_id, o.outcome, o.r_multiple, o.resolved_price, o.resolved_at,
                                    s.symbol, s.trade_mode, s.tier, s.direction
                             FROM qtx_signal_outcomes o JOIN qtx_signals s ON s.signal_id = o.signal_id
                             WHERE o.outcome_id > :1 ORDER BY o.outcome_id FETCH FIRST :2 ROWS ONLY""",
                          [outcome_id, limit])
        return pd.DataFrame(rows, columns=OUTCOME_COLS)

    def feed_freshness(self) -> pd.DataFrame:
        rows = self._rows("""SELECT symbol, MAX(asset_class), MAX(bar_ts) FROM qtx_ohlcv
                             WHERE timeframe = 'M15' GROUP BY symbol""")
        return pd.DataFrame(rows, columns=["symbol", "asset_class", "last_bar_ts"])

    def core_states(self) -> pd.DataFrame:
        rows = self._rows("SELECT symbol, trade_mode, current_tier, updated_at FROM qtx_signal_state")
        return pd.DataFrame(rows, columns=["symbol", "trade_mode", "tier", "updated_at"])

    def unlabelled_confirmed(self) -> pd.DataFrame:
        rows = self._rows("""SELECT s.signal_id, s.symbol, s.trade_mode, s.indicator_snapshot_json
                             FROM qtx_signals s
                             WHERE s.tier = 'CONFIRMED'
                               AND NOT EXISTS (SELECT 1 FROM qtx_signal_outcomes o WHERE o.signal_id = s.signal_id)""")
        return pd.DataFrame([(a, b, c, as_json(d).get("close_ts")) for a, b, c, d in rows],
                            columns=["signal_id", "symbol", "trade_mode", "close_ts"])
