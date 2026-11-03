from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from config import FeedConfig, SymbolSpec, load_config
from conftest import FakeMT5
from models import NewsEvent
from pipeline import SourcePool, seconds_until_next_bar, sync_all, sync_news
from price_sources import build_price_source
from price_sources.mt5 import MT5Source
from storage.preview import PreviewStore

V75 = SymbolSpec("V75", "Volatility 75 Index", "SYNTHETIC")
EUR = SymbolSpec("EURUSD", "EURUSD", "FOREX")
GHOST = SymbolSpec("GHOST", "Ghost Index", "SYNTHETIC")


@pytest.fixture
def cfg(tmp_path) -> FeedConfig:
    base = load_config()
    return replace(base, symbols=[V75, EUR], backfill_days=3, preview_dir=tmp_path,
                   mt5=replace(base.mt5, broker_tz="", broker_utc_offset_hours=None))


def frozen_now(clock):
    return lambda: datetime.fromtimestamp(clock(), tz=timezone.utc).replace(tzinfo=None)


def fake_factory(fake):
    def factory(name, cfg):
        assert name == "mt5"
        return MT5Source(cfg.mt5, mt5_module=fake, clock=fake.clock)
    return factory


def test_backfill_then_incremental_is_idempotent(cfg, clock, tmp_path):
    fake = FakeMT5(clock, offset_s=7200)
    pool = SourcePool(cfg, factory=fake_factory(fake))
    store = PreviewStore(tmp_path)

    now = frozen_now(clock)

    total, failed = sync_all(pool, store, cfg, now=now)
    assert failed == []
    # now = 12:07:30, start = 3 days earlier -> first bar 10-31 12:15, last closed bar 11-03 11:45
    assert total == 2 * 287  # (3 days * 96) - 2 bars at the front + the inclusive endpoint

    again, _ = sync_all(pool, store, cfg, now=now)
    assert again == 0  # nothing new, nothing duplicated

    clock.advance(30 * 60)  # two more bars close
    later, _ = sync_all(pool, store, cfg, now=now)
    assert later == 2 * 2

    saved = pd.read_csv(tmp_path / "ohlcv_V75_M15.csv", parse_dates=["bar_ts"])
    assert saved["bar_ts"].is_unique
    assert saved["bar_ts"].max() == pd.Timestamp("2026-11-03 12:15")


def test_missing_symbol_does_not_stop_others(cfg, clock, tmp_path):
    cfg = replace(cfg, symbols=[GHOST, V75])
    pool = SourcePool(cfg, factory=fake_factory(FakeMT5(clock)))
    total, failed = sync_all(pool, PreviewStore(tmp_path), cfg, now=frozen_now(clock))
    assert failed == ["GHOST"]
    assert total > 0


def test_one_connection_per_source(cfg, clock):
    calls = []
    fake = FakeMT5(clock)

    def factory(name, c):
        calls.append(name)
        return MT5Source(c.mt5, mt5_module=fake, clock=clock)

    pool = SourcePool(cfg, factory=factory)
    pool.source_for(V75)
    pool.source_for(EUR)
    assert calls == ["mt5"]


def test_unknown_price_source_is_rejected(cfg):
    with pytest.raises(ValueError, match="Unknown price source"):
        build_price_source("ccxt", cfg)


def test_news_sync_deduplicates(tmp_path):
    class Static:
        name = "static"

        def fetch(self):
            return [NewsEvent(datetime(2026, 11, 6, 13, 30), "USD", "HIGH", "Non-Farm Employment Change")]

    store = PreviewStore(tmp_path)
    assert sync_news(Static(), store) == 1
    assert sync_news(Static(), store) == 0


def test_seconds_until_next_bar():
    now = datetime(2026, 11, 3, 12, 7, 30, tzinfo=timezone.utc)
    assert seconds_until_next_bar(now, 15, 20) == pytest.approx(7.5 * 60 + 20)
