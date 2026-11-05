"""Tests for `verify` — mostly proving each check actually catches the problem it's for."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from config import SymbolSpec, load_config
from conftest import FakeMT5
from pipeline import SourcePool, sync_all
from price_sources.mt5 import MT5Source
from storage.preview import PreviewStore
from verification import broker, integrity, reference, run_verification
from verification.base import Status

V75 = SymbolSpec("V75", "Volatility 75 Index", "SYNTHETIC")
EUR = SymbolSpec("EURUSD", "EURUSD", "FOREX")
BTC = SymbolSpec("BTCUSD", "BTCUSD", "CRYPTO")
NOW = datetime(2026, 11, 4, 12, 7, 30)  # a Wednesday


def make_bars(n=400, end=datetime(2026, 11, 4, 11, 45), symbol="V75", seed=1, freq="15min"):
    rng = np.random.default_rng(seed)
    ts = pd.date_range(end=end, periods=n, freq=freq)
    close = 1000 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
    open_ = np.r_[close[0], close[:-1]]
    wiggle = np.abs(rng.normal(0, 0.001, n)) * close
    return pd.DataFrame({"symbol": symbol, "asset_class": "SYNTHETIC", "timeframe": "M15", "bar_ts": ts,
                         "open_price": open_, "high_price": np.maximum(open_, close) + wiggle,
                         "low_price": np.minimum(open_, close) - wiggle, "close_price": close,
                         "volume": 10.0, "price_side": "BID"})


def by_check(results):
    return {r.check: r for r in results}


# -- layer 1: integrity ---------------------------------------------------------------
def test_clean_data_passes_everything():
    res = by_check(integrity.check_integrity(make_bars(), V75, NOW))
    assert {k: r.status for k, r in res.items()} == {
        "has data": Status.PASS, "valid prices & times": Status.PASS, "gaps": Status.PASS,
        "frozen/spikes": Status.PASS, "fresh": Status.PASS}


def test_empty_is_a_failure():
    assert integrity.check_integrity(make_bars().iloc[0:0], V75, NOW)[0].status == Status.FAIL


@pytest.mark.parametrize("corrupt, expect", [
    (lambda df: df.assign(high_price=df["low_price"] - 1), "high/low"),
    (lambda df: df.assign(close_price=-df["close_price"]), "zero/negative"),
    (lambda df: df.assign(bar_ts=df["bar_ts"] + pd.Timedelta(minutes=7)), "15-minute boundary"),
    (lambda df: df.assign(bar_ts=df["bar_ts"] + pd.Timedelta(hours=2)), "future"),
])
def test_impossible_data_fails(corrupt, expect):
    res = by_check(integrity.check_integrity(corrupt(make_bars()), V75, NOW))["valid prices & times"]
    assert res.status == Status.FAIL and expect in res.detail


def test_gap_in_24_7_symbol_warns():
    df = make_bars().drop(index=range(100, 110))
    res = by_check(integrity.check_integrity(df, V75, NOW))["gaps"]
    assert res.status == Status.WARN and "10 missing bars" in res.detail


def test_forex_weekend_gap_is_expected_but_midweek_gap_is_not():
    week = make_bars(n=7 * 96, end=datetime(2026, 11, 4, 11, 45))  # Thu 29 Oct → Wed 4 Nov
    week["asset_class"] = "FOREX"
    ts = week["bar_ts"]
    weekend = (ts >= "2026-10-30 21:00") & (ts < "2026-11-01 22:00")  # Fri 21:00 → Sun 22:00
    fx = week[~weekend].reset_index(drop=True)
    assert by_check(integrity.check_integrity(fx, EUR, NOW))["gaps"].status == Status.PASS

    midweek = fx[~((fx["bar_ts"] >= "2026-11-03 09:00") & (fx["bar_ts"] < "2026-11-03 13:00"))]
    assert by_check(integrity.check_integrity(midweek, EUR, NOW))["gaps"].status == Status.WARN


def test_frozen_prices_and_spikes_warn():
    df = make_bars()
    df.loc[50:60, ["open_price", "high_price", "low_price", "close_price"]] = 1000.0
    assert "frozen" in by_check(integrity.check_integrity(df, V75, NOW))["frozen/spikes"].detail
    df = make_bars()
    df.loc[200, "high_price"] *= 1.5
    assert "range" in by_check(integrity.check_integrity(df, V75, NOW))["frozen/spikes"].detail


def test_freshness():
    stale = make_bars(end=datetime(2026, 11, 4, 9, 0))
    assert by_check(integrity.check_integrity(stale, V75, NOW))["fresh"].status == Status.WARN
    saturday = datetime(2026, 11, 7, 12, 0)
    fx = make_bars(end=datetime(2026, 11, 6, 20, 45))
    assert by_check(integrity.check_integrity(fx, EUR, saturday))["fresh"].status == Status.SKIP


# -- layer 2: broker ------------------------------------------------------------------
def test_round_trip_detects_changes_missing_bars_and_shifts():
    mt5 = make_bars()
    assert broker.round_trip("V75", mt5.copy(), mt5).status == Status.PASS

    altered = mt5.copy()
    altered.loc[10, "close_price"] += 0.01
    assert broker.round_trip("V75", altered, mt5).status == Status.FAIL

    assert broker.round_trip("V75", mt5.drop(index=[5, 6]), mt5).status == Status.WARN

    shifted = mt5.assign(bar_ts=mt5["bar_ts"] + pd.Timedelta(minutes=15))
    res = broker.round_trip("V75", shifted, mt5)
    assert res.status == Status.FAIL


def test_rounding_to_six_decimals_is_not_a_mismatch():
    mt5 = make_bars()
    stored = mt5.copy()
    for col in broker.PRICE_COLS:
        stored[col] = stored[col].round(6)
    assert broker.round_trip("V75", stored, mt5).status == Status.PASS


def h1_from(m15, offset_min=0):
    """Independent H1 build via pandas resample (what a broker would produce)."""
    shifted = m15.set_index(m15["bar_ts"] + pd.Timedelta(minutes=offset_min))
    h1 = shifted.resample("1h").agg({"open_price": "first", "high_price": "max", "low_price": "min",
                                     "close_price": "last", "volume": "sum"}).dropna().reset_index()
    h1["bar_ts"] = h1["bar_ts"] - pd.Timedelta(minutes=offset_min)
    return h1


@pytest.mark.parametrize("offset_min", [0, 330])  # whole-hour broker, and a UTC+5:30 broker
def test_h1_rebuild_matches_broker_h1(offset_min):
    m15 = make_bars(end=datetime(2026, 11, 4, 11, 45) - timedelta(minutes=offset_min))
    assert broker.h1_rebuild("V75", m15, h1_from(m15, offset_min)).status == Status.PASS


def test_h1_rebuild_catches_a_missing_m15_bar():
    m15 = make_bars()
    h1 = h1_from(m15)
    first_of_hour = m15.index[m15["bar_ts"] == h1["bar_ts"].iloc[20]][0]
    res = broker.h1_rebuild("V75", m15.drop(index=first_of_hour), h1)
    assert res.status == Status.FAIL and "don't match" in res.detail


# -- layer 3: independent reference ---------------------------------------------------------
def coinbase_like(n=300, seed=7):
    bars = make_bars(n=n, seed=seed)
    return pd.Series(bars["close_price"].to_numpy(), index=bars["bar_ts"])


def deriv_from(ref, shift_bars=0, markup=1.0005, seed=3):
    """Deriv's CFD: same moves plus a little noise, optionally mislabelled in time."""
    rng = np.random.default_rng(seed)
    close = ref.to_numpy() * markup * (1 + rng.normal(0, 0.0003, len(ref)))
    ts = ref.index + pd.Timedelta(minutes=15 * shift_bars)
    return pd.DataFrame({"bar_ts": ts, "close_price": close})


def test_reference_clock_passes_when_aligned():
    ref = coinbase_like()
    res = by_check(reference.check_clock("BTCUSD", deriv_from(ref), ref))
    assert res["UTC clock vs Coinbase"].status == Status.PASS
    assert res["price level vs Coinbase"].status == Status.PASS


@pytest.mark.parametrize("shift_bars, words", [(8, "120 min too late"), (-4, "60 min too early")])
def test_reference_clock_catches_wrong_broker_offset(shift_bars, words):
    ref = coinbase_like()
    res = by_check(reference.check_clock("BTCUSD", deriv_from(ref, shift_bars), ref))["UTC clock vs Coinbase"]
    assert res.status == Status.FAIL and words in res.detail


def test_reference_price_level_warns_on_big_difference():
    ref = coinbase_like()
    res = by_check(reference.check_clock("BTCUSD", deriv_from(ref, markup=1.03), ref))
    assert res["price level vs Coinbase"].status == Status.WARN


def test_coinbase_parser():
    class Resp:
        def raise_for_status(self):
            pass

        def json(self):  # newest first, [time, low, high, open, close, volume]
            return [[1762257600, 1, 3, 2, 2.5, 10], [1762256700, 1, 3, 2, 2.2, 10]]

    class Http:
        def get(self, url, **kw):
            self.url, self.kw = url, kw
            return Resp()

    http = Http()
    s = reference.fetch_coinbase("BTC-USD", datetime(2026, 11, 4, 12, 0), http=http)
    assert list(s.to_numpy()) == [2.2, 2.5] and s.index.is_monotonic_increasing
    assert http.kw["params"]["granularity"] == 900 and "BTC-USD" in http.url


# -- end to end: real pipeline into preview store, then verify -----------------------------
def test_verify_end_to_end_with_fake_broker(clock, tmp_path):
    base = load_config()
    cfg = replace(base, symbols=[V75, BTC], backfill_days=3, preview_dir=tmp_path,
                  mt5=replace(base.mt5, broker_tz="", broker_utc_offset_hours=None))
    fake = FakeMT5(clock, offset_s=7200, symbols=("Volatility 75 Index", "BTCUSD"))
    pool = SourcePool(cfg, factory=lambda name, c: MT5Source(c.mt5, mt5_module=fake, clock=clock))
    now = lambda: datetime.fromtimestamp(clock(), tz=timezone.utc).replace(tzinfo=None)  # noqa: E731
    store = PreviewStore(tmp_path)
    sync_all(pool, store, cfg, now=now)

    def fake_coinbase(product, end, http=None):  # an "exchange" that agrees with the broker
        bars = store.load_bars("BTCUSD", "M15")
        return pd.Series(bars["close_price"].to_numpy() * 1.001, index=bars["bar_ts"])

    report = run_verification(cfg, store, pool, days=3, fetch_reference=fake_coinbase, now=now)
    statuses = {(r.symbol, r.check): r.status for r in report.results}
    assert statuses[("V75", "matches MT5 (M15)")] == Status.PASS
    assert statuses[("V75", "rebuilds MT5 H1")] == Status.PASS
    assert statuses[("BTCUSD", "UTC clock vs Coinbase")] == Status.PASS
    assert report.worst == Status.PASS, [r for r in report.results if r.status != Status.PASS]
    sample = report.samples["V75"]
    assert (sample["broker_clock"] - sample["utc"] == pd.Timedelta(hours=2)).all()


def test_verify_without_broker_or_network_still_runs_integrity(tmp_path):
    cfg = replace(load_config(), symbols=[V75], preview_dir=tmp_path)
    store = PreviewStore(tmp_path)
    store.insert_ohlcv(make_bars(end=datetime(2026, 11, 4, 11, 45)))
    report = run_verification(cfg, store, pool=None, use_reference=False, days=7, now=lambda: NOW)
    statuses = {r.check: r.status for r in report.results}
    assert statuses["valid prices & times"] == Status.PASS
    assert statuses["matches MT5"] == Status.SKIP
