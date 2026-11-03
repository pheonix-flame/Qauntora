from datetime import datetime

import pandas as pd
import pytest

import config
from config import NewsConfig
from news_sources import build_news_provider
from news_sources import fmp, forexfactory
from news_sources.base import NullProvider
from storage.sqlserver import SqlServerStore, ohlcv_rows


def test_forexfactory_parse_converts_to_utc_and_skips_holidays():
    payload = [
        {"title": "Non-Farm Employment Change", "country": "USD", "date": "2026-11-06T08:30:00-05:00",
         "impact": "High", "forecast": "", "previous": ""},
        {"title": "Bank Holiday", "country": "JPY", "date": "2026-11-03T00:00:00-05:00", "impact": "Holiday"},
        {"title": "Broken", "country": "EUR", "date": "not a date", "impact": "Low"},
    ]
    events = forexfactory.parse(payload)
    assert len(events) == 1
    e = events[0]
    assert (e.event_ts, e.currency, e.impact) == (datetime(2026, 11, 6, 13, 30), "USD", "HIGH")


def test_fmp_parse():
    payload = [{"date": "2026-11-06 13:30:00", "country": "US", "currency": "USD",
                "event": "Nonfarm Payrolls", "impact": "High"},
               {"date": "2026-11-06 14:00:00", "country": "US", "event": "Something", "impact": "None"}]
    events = fmp.parse(payload)
    assert [(e.event_ts, e.currency, e.impact) for e in events] == [(datetime(2026, 11, 6, 13, 30), "USD", "HIGH")]


def test_news_provider_registry():
    assert isinstance(build_news_provider(NewsConfig("none", "", 6)), NullProvider)
    with pytest.raises(ValueError, match="FMP_API_KEY"):
        build_news_provider(NewsConfig("fmp", "", 6))
    with pytest.raises(ValueError, match="Unknown NEWS_PROVIDER"):
        build_news_provider(NewsConfig("finnhub", "", 6))


def test_symbol_filter_and_broker_name_override(monkeypatch):
    monkeypatch.setenv("QTX_SYMBOLS", "v75, eurusd")
    monkeypatch.setenv("QTX_BROKER_NAME_V75", "Volatility 75 (1s) Index")
    specs = config._load_symbols()
    assert [s.key for s in specs] == ["V75", "EURUSD"]
    assert specs[0].broker_symbol == "Volatility 75 (1s) Index"
    assert all(s.source == "mt5" for s in specs)


def test_unknown_symbol_key_rejected(monkeypatch):
    monkeypatch.setenv("QTX_SYMBOLS", "DOGE")
    with pytest.raises(ValueError):
        config._load_symbols()


def test_conn_str_windows_auth_by_default(monkeypatch):
    for k in ["SQLSERVER_CONN_STR", "DB_USER", "DB_SERVER", "DB_NAME"]:
        monkeypatch.delenv(k, raising=False)
    s = config._build_conn_str()
    assert "Trusted_Connection=yes" in s and r"SERVER=localhost\SQLEXPRESS" in s


class _Cursor:
    def __init__(self, log):
        self.log, self.rowcount, self.fast_executemany = log, 0, False

    def execute(self, sql, *params):
        self.log.append(("execute", " ".join(sql.split())[:40]))
        if sql.strip().startswith("INSERT INTO qtx_ohlcv"):
            self.rowcount = 2

    def executemany(self, sql, rows):
        self.log.append(("executemany", len(rows), self.fast_executemany))


class _Conn:
    def __init__(self):
        self.log = []

    def cursor(self):
        return _Cursor(self.log)

    def commit(self):
        self.log.append(("commit",))

    def rollback(self):
        self.log.append(("rollback",))

    def close(self):
        pass


def test_sqlserver_insert_uses_stage_then_insert_only():
    conn = _Conn()
    store = SqlServerStore("x", connect=lambda *_a, **_k: conn)
    df = pd.DataFrame({"symbol": ["V75"] * 2, "asset_class": "SYNTHETIC", "timeframe": "M15",
                       "bar_ts": pd.to_datetime(["2026-11-03 11:30", "2026-11-03 11:45"]),
                       "open_price": 1.0, "high_price": 2.0, "low_price": 0.5, "close_price": 1.5,
                       "volume": 10.0, "price_side": "BID"})
    assert store.insert_ohlcv(df) == 2
    assert ("executemany", 2, True) in conn.log
    assert conn.log[-1] == ("commit",)
    assert not any("UPDATE" in str(entry) or "MERGE" in str(entry) for entry in conn.log)
    row = ohlcv_rows(df)[0]
    assert isinstance(row[3], datetime) and isinstance(row[4], float)
