"""Oracle backend for Core (python-oracledb thin mode)."""
from __future__ import annotations

import json
from datetime import datetime

import pandas as pd

from config import DBConfig, SignalConfig, validate_signal_config
from storage.base import BAR_COLUMNS, CoreStore, tidy_bars


def _json(v) -> dict:
    """JSON column value -> dict. Oracle 23ai+ may hand back an IS JSON CLOB already parsed."""
    if v is None:
        return {}
    if isinstance(v, (dict, list)):
        return v
    v = v.read() if hasattr(v, "read") else v
    return json.loads(v) if v else {}


class OracleCoreStore(CoreStore):
    _UPSERT_STATE = """
        MERGE INTO qtx_signal_state t
        USING (SELECT :symbol AS symbol, :trade_mode AS trade_mode FROM dual) s
        ON (t.symbol = s.symbol AND t.trade_mode = s.trade_mode)
        WHEN MATCHED THEN UPDATE SET
            current_tier = :tier, direction = :direction, score = :score,
            indicator_snapshot_json = :snap, notes = :notes,
            updated_at = SYS_EXTRACT_UTC(SYSTIMESTAMP)
        WHEN NOT MATCHED THEN INSERT
            (symbol, trade_mode, current_tier, direction, score, indicator_snapshot_json, notes)
        VALUES (:symbol, :trade_mode, :tier, :direction, :score, :snap, :notes)"""
    _INSERT_SIGNAL = """
        INSERT INTO qtx_signals (symbol, trade_mode, tier, direction, score, entry_price, stop_loss,
                                 take_profit, lot_size, indicator_snapshot_json)
        VALUES (:symbol, :trade_mode, :tier, :direction, :score, :entry, :stop, :target, NULL, :snap)
        RETURNING signal_id INTO :new_id"""

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

    def commit(self) -> None:
        self.conn.commit()

    # -- reads ---------------------------------------------------------------
    def load_m15(self, symbol: str, since: datetime | None = None) -> pd.DataFrame:
        cur = self.conn.cursor()
        cur.arraysize = 10_000
        cur.execute(f"SELECT {', '.join(BAR_COLUMNS)} FROM qtx_ohlcv WHERE symbol = :1 AND timeframe = 'M15' "
                    "AND bar_ts >= :2 ORDER BY bar_ts", [symbol, since or datetime(1970, 1, 1)])
        return tidy_bars(pd.DataFrame(cur.fetchall(), columns=BAR_COLUMNS))

    def news_events(self, start: datetime, end: datetime) -> pd.DataFrame:
        cur = self.conn.cursor()
        cur.execute("SELECT event_ts, currency, impact, title FROM qtx_news_events "
                    "WHERE event_ts BETWEEN :1 AND :2", [start, end])
        return pd.DataFrame(cur.fetchall(), columns=["event_ts", "currency", "impact", "title"])

    def approved_config(self, symbol: str, mode: str, base: SignalConfig) -> SignalConfig | None:
        cur = self.conn.cursor()
        cur.execute("""SELECT version_id, weights_json, tier2_threshold, tier3_threshold
                       FROM qtx_config_versions
                       WHERE symbol = :1 AND trade_mode = :2 AND approved_flag = 'Y'
                       ORDER BY approved_at DESC NULLS LAST, version_id DESC
                       FETCH FIRST 1 ROWS ONLY""", [symbol, mode])
        row = cur.fetchone()
        if row is None:
            return None
        vid, wjson, t2, t3 = row
        body = _json(wjson)
        return validate_signal_config(float(t2), float(t3), body.get("weights", {}), body.get("params", {}),
                                      version_id=int(vid), base=base)

    def get_state(self, symbol: str, mode: str) -> dict | None:
        cur = self.conn.cursor()
        cur.execute("SELECT current_tier, direction, score, indicator_snapshot_json, notes "
                    "FROM qtx_signal_state WHERE symbol = :1 AND trade_mode = :2", [symbol, mode])
        row = cur.fetchone()
        if row is None:
            return None
        return {"tier": row[0], "direction": row[1], "score": row[2],
                "snapshot": _json(row[3]), "notes": row[4]}

    def list_states(self) -> pd.DataFrame:
        cur = self.conn.cursor()
        cur.execute("SELECT symbol, trade_mode, current_tier, direction, score, notes, updated_at "
                    "FROM qtx_signal_state ORDER BY symbol, trade_mode")
        return pd.DataFrame(cur.fetchall(), columns=["symbol", "mode", "tier", "direction", "score", "notes",
                                                     "updated_at"])

    # -- writes ----------------------------------------------------------------
    def save_state(self, symbol, mode, tier, direction, score, snapshot, notes) -> None:
        cur = self.conn.cursor()
        cur.setinputsizes(snap=self._db.DB_TYPE_CLOB)
        cur.execute(self._UPSERT_STATE, symbol=symbol, trade_mode=mode, tier=tier, direction=direction,
                    score=round(float(score), 2), snap=json.dumps(snapshot), notes=(notes or "")[:400])

    def append_signal(self, symbol, mode, tier, direction, score, entry, stop, target, snapshot) -> int:
        cur = self.conn.cursor()
        new_id = cur.var(self._db.NUMBER)
        cur.setinputsizes(snap=self._db.DB_TYPE_CLOB)
        cur.execute(self._INSERT_SIGNAL, symbol=symbol, trade_mode=mode, tier=tier, direction=direction,
                    score=round(float(score), 2), entry=entry, stop=stop, target=target,
                    snap=json.dumps(snapshot), new_id=new_id)
        value = new_id.getvalue()
        return int(value[0] if isinstance(value, list) else value)
