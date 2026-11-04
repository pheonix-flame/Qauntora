from datetime import datetime

import pytest

import config
from config import NewsConfig
from news_sources import build_news_provider
from news_sources import fmp, forexfactory
from news_sources.base import NullProvider


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
