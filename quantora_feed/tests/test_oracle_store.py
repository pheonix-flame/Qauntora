"""OracleStore tests against a fake python-oracledb driver.

The fake implements the one behaviour that matters — MERGE ... WHEN NOT MATCHED
inserts only rows whose key isn't present — so idempotency, batching, rowcount
and commit/rollback are exercised for real. SQL syntax itself is only checked
against a real database (run `python main.py init-db` / `check` on your machine).
"""
from datetime import datetime
from types import SimpleNamespace

import pandas as pd
import pytest

import config
from config import DBConfig
from models import NewsEvent
from storage import oracle
from storage.oracle import OracleStore, split_sql_script


class FakeCursor:
    def __init__(self, db):
        self.db, self.rowcount, self.inputsizes = db, 0, None

    def setinputsizes(self, *types):
        self.inputsizes = types

    def execute(self, sql, params=None):
        self.db.executed.append(sql)
        if "user_tables" in sql:
            self._result = [(1 if "qtx_ohlcv" in self.db.tables else 0,)]
        elif sql.startswith("SELECT MAX(bar_ts)"):
            symbol, tf = params
            ts = [k[2] for k in self.db.ohlcv if k[0] == symbol and k[1] == tf]
            self._result = [(max(ts) if ts else None,)]
        elif sql.startswith("CREATE TABLE"):
            self.db.tables.add(sql.split()[2].lower())

    def executemany(self, sql, rows):
        self.db.batches.append(len(rows))
        if self.db.fail_next:
            self.db.fail_next = False
            raise RuntimeError("ORA-00001 simulated")
        added = 0
        if "MERGE INTO qtx_ohlcv" in sql:
            assert self.inputsizes[3] == "TIMESTAMP", "bar_ts must bind as TIMESTAMP"
            for r in rows:
                key = (r[0], r[2], r[3])
                if key not in self.db.ohlcv:
                    self.db.pending[key] = r
                    added += 1
        elif "MERGE INTO qtx_news_events" in sql:
            for r in rows:
                key = (r[0], r[1], r[3])
                if key not in self.db.news:
                    self.db.pending_news.add(key)
                    added += 1
        self.rowcount = added

    def fetchone(self):
        return self._result[0]


class FakeConn:
    version = "23.26.0.0.0"

    def __init__(self):
        self.ohlcv, self.pending = {}, {}
        self.news, self.pending_news = set(), set()
        self.tables, self.executed, self.batches = set(), [], []
        self.commits = self.rollbacks = 0
        self.fail_next = False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.ohlcv.update(self.pending)
        self.news |= self.pending_news
        self.pending, self.pending_news = {}, set()
        self.commits += 1

    def rollback(self):
        self.pending, self.pending_news = {}, set()
        self.rollbacks += 1

    def close(self):
        pass


@pytest.fixture
def fake_driver():
    conn = FakeConn()
    calls = []

    def connect(**params):
        calls.append(params)
        return conn

    return SimpleNamespace(connect=connect, conn=conn, calls=calls,
                           DB_TYPE_TIMESTAMP="TIMESTAMP", DB_TYPE_BINARY_DOUBLE="BINARY_DOUBLE")


DB = DBConfig(user="quantora", password="secret", dsn="localhost:1521/FREEPDB1")


def bars(times, symbol="V75"):
    return pd.DataFrame({"symbol": symbol, "asset_class": "SYNTHETIC", "timeframe": "M15",
                         "bar_ts": pd.to_datetime(times), "open_price": 1.0, "high_price": 2.0,
                         "low_price": 0.5, "close_price": 1.5, "volume": 10.0, "price_side": "BID"})


def test_connect_params_local_and_wallet(fake_driver):
    OracleStore(DB, driver=fake_driver)
    assert fake_driver.calls[-1] == {"user": "quantora", "password": "secret", "dsn": "localhost:1521/FREEPDB1"}
    wallet = DBConfig("quantora", "secret", "mydb_high", wallet_dir=r"C:\wallet", wallet_password="w")
    OracleStore(wallet, driver=fake_driver)
    assert fake_driver.calls[-1]["config_dir"] == r"C:\wallet"
    assert fake_driver.calls[-1]["wallet_password"] == "w"


def test_empty_password_is_rejected_before_connecting(fake_driver):
    with pytest.raises(ValueError, match="ORACLE_PASSWORD"):
        OracleStore(DBConfig("quantora", "", "x"), driver=fake_driver)
    assert fake_driver.calls == []


def test_insert_ohlcv_is_idempotent_and_tracks_latest(fake_driver):
    store = OracleStore(DB, driver=fake_driver)
    assert store.latest_bar_ts("V75", "M15") is None
    assert store.insert_ohlcv(bars(["2026-11-03 11:30", "2026-11-03 11:45"])) == 2
    assert store.insert_ohlcv(bars(["2026-11-03 11:45", "2026-11-03 12:00"])) == 1
    assert store.latest_bar_ts("V75", "M15") == datetime(2026, 11, 3, 12, 0)
    assert store.latest_bar_ts("EURUSD", "M15") is None


def test_large_insert_is_batched(fake_driver, monkeypatch):
    monkeypatch.setattr(oracle, "BATCH_SIZE", 100)
    store = OracleStore(DB, driver=fake_driver)
    times = pd.date_range("2026-10-01", periods=250, freq="15min")
    assert store.insert_ohlcv(bars(times)) == 250
    assert fake_driver.conn.batches == [100, 100, 50]
    assert fake_driver.conn.commits == 1  # one transaction per call


def test_failed_insert_rolls_back_everything(fake_driver):
    store = OracleStore(DB, driver=fake_driver)
    fake_driver.conn.fail_next = True
    with pytest.raises(RuntimeError):
        store.insert_ohlcv(bars(["2026-11-03 11:30"]))
    assert fake_driver.conn.rollbacks == 1 and fake_driver.conn.ohlcv == {}


def test_insert_news_dedupes(fake_driver):
    store = OracleStore(DB, driver=fake_driver)
    nfp = NewsEvent(datetime(2026, 11, 6, 13, 30), "USD", "HIGH", "Non-Farm Employment Change")
    cpi = NewsEvent(datetime(2026, 11, 12, 13, 30), "USD", "HIGH", "CPI m/m")
    assert store.insert_news([nfp]) == 1
    assert store.insert_news([nfp, cpi]) == 1


def test_init_schema_runs_each_statement_once(fake_driver):
    store = OracleStore(DB, driver=fake_driver)
    cfg = config.load_config()
    assert store.schema_exists() is False
    assert store.init_schema(cfg.schema_file) is True
    ddl = [s for s in fake_driver.conn.executed if s.startswith("CREATE")]
    assert all(";" not in s for s in ddl)
    assert store.schema_exists() is True
    assert store.init_schema(cfg.schema_file) is False  # second run is a no-op


def test_real_schema_file_splits_into_expected_statements():
    stmts = split_sql_script(config.load_config().schema_file.read_text(encoding="utf-8"))
    tables = [s.split()[2] for s in stmts if s.startswith("CREATE TABLE")]
    assert tables == ["qtx_ohlcv", "qtx_news_events", "qtx_config_versions", "qtx_signal_state",
                      "qtx_signals", "qtx_signal_outcomes", "qtx_trades", "qtx_account_snapshots"]
    assert sum(s.startswith("CREATE INDEX") for s in stmts) == 8
    assert not any("--" in s for s in stmts)
    # audit timestamps must default to UTC, not the DB server's local time
    assert not any("DEFAULT SYSTIMESTAMP" in s for s in stmts)


def test_split_keeps_dashes_inside_string_literals():
    stmts = split_sql_script("INSERT INTO t VALUES ('a--b'); -- trailing comment\nSELECT 1 FROM dual;")
    assert stmts == ["INSERT INTO t VALUES ('a--b')", "SELECT 1 FROM dual"]
