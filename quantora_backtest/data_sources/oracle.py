"""Read qtx_ohlcv from Oracle (python-oracledb thin mode)."""
from __future__ import annotations

import pandas as pd

from config import DBConfig
from data_sources.base import BAR_COLUMNS, BarSource, tidy


class OracleBarSource(BarSource):
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
        return f"Oracle {self.conn.version} at {self._dsn} (read-only use)"

    def close(self) -> None:
        self.conn.close()

    def load_m15(self, symbol: str) -> pd.DataFrame:
        cur = self.conn.cursor()
        cur.arraysize = 10_000
        cur.execute(f"SELECT {', '.join(BAR_COLUMNS)} FROM qtx_ohlcv "
                    "WHERE symbol = :1 AND timeframe = 'M15' ORDER BY bar_ts", [symbol])
        return tidy(pd.DataFrame(cur.fetchall(), columns=BAR_COLUMNS))

    def inventory(self) -> pd.DataFrame:
        cur = self.conn.cursor()
        cur.execute("SELECT symbol, COUNT(*), MIN(bar_ts), MAX(bar_ts) FROM qtx_ohlcv "
                    "WHERE timeframe = 'M15' GROUP BY symbol ORDER BY symbol")
        return pd.DataFrame(cur.fetchall(), columns=["symbol", "bars", "first", "last"])
