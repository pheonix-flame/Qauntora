from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from config import MT5Config, SymbolSpec
from conftest import FakeMT5
from price_sources.mt5 import MT5Source, rates_to_frame, server_times_to_utc

V75 = SymbolSpec("V75", "Volatility 75 Index", "SYNTHETIC")
EUR = SymbolSpec("EURUSD", "EURUSD", "FOREX")


def mt5_cfg(**kw):
    base = dict(login=None, password="", server="", terminal_path="", broker_tz="", broker_utc_offset_hours=None)
    base.update(kw)
    return MT5Config(**base)


def epoch(s):
    return int(pd.Timestamp(s).timestamp())


def test_fixed_offset_conversion():
    out = server_times_to_utc(np.array([epoch("2026-01-05 12:00")]), "", 7200)
    assert out[0] == pd.Timestamp("2026-01-05 10:00")


def test_iana_zone_handles_dst_in_history():
    # Helsinki is UTC+3 in July, UTC+2 in January — a fixed offset would get one of them wrong.
    out = server_times_to_utc(np.array([epoch("2026-07-01 12:00"), epoch("2026-01-05 12:00")]),
                              "Europe/Helsinki", 0)
    assert list(out) == [pd.Timestamp("2026-07-01 09:00"), pd.Timestamp("2026-01-05 10:00")]


def test_rates_to_frame_keeps_only_closed_bars_after_start():
    times = np.array([epoch(f"2026-11-03 {t}") for t in ["11:30", "11:45", "12:00"]], dtype="int64")
    rates = np.zeros(3, dtype=[("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"),
                               ("close", "<f8"), ("tick_volume", "<u8")])
    rates["time"] = times
    df = rates_to_frame(rates, V75, "M15", "", 0,
                        start_utc=datetime(2026, 11, 3, 11, 45), now_utc=datetime(2026, 11, 3, 12, 7))
    # 11:30 is before start; 12:00 is still forming at 12:07 -> only 11:45 survives
    assert list(df["bar_ts"]) == [pd.Timestamp("2026-11-03 11:45")]
    assert df["price_side"].iloc[0] == "BID"
    assert df["symbol"].iloc[0] == "V75"


@pytest.mark.parametrize("offset_s", [0, 7200, 10800, -18000])
def test_offset_autodetected_from_live_tick(clock, offset_s):
    fake = FakeMT5(clock, offset_s=offset_s)
    src = MT5Source(mt5_cfg(), mt5_module=fake, clock=clock)
    src.connect([EUR, V75])
    assert src.fixed_offset_s == offset_s


def test_stale_tick_falls_back_to_configured_offset(clock):
    fake = FakeMT5(clock, offset_s=7200, tick_lag_s=600)  # 10-minute-old tick: unusable
    src = MT5Source(mt5_cfg(broker_utc_offset_hours=2), mt5_module=fake, clock=clock)
    src.connect([V75])
    assert src.fixed_offset_s == 7200


def test_stale_tick_without_fallback_raises(clock):
    fake = FakeMT5(clock, offset_s=7200, tick_lag_s=600)
    src = MT5Source(mt5_cfg(), mt5_module=fake, clock=clock)
    with pytest.raises(RuntimeError, match="BROKER_TZ"):
        src.connect([V75])


@pytest.mark.parametrize("offset_s", [0, 7200])
def test_fetch_bars_returns_contiguous_closed_utc_bars(clock, offset_s):
    fake = FakeMT5(clock, offset_s=offset_s)
    src = MT5Source(mt5_cfg(), mt5_module=fake, clock=clock)
    src.connect([V75])
    start = datetime(2026, 9, 1)  # > 30 days back -> exercises chunking
    df = src.fetch_bars(V75, start)

    assert len(fake.range_calls) > 1
    assert df["bar_ts"].min() == pd.Timestamp(start)
    # now is 12:07:30 UTC -> last closed bar opened at 11:45
    assert df["bar_ts"].max() == pd.Timestamp("2026-11-03 11:45")
    assert df["bar_ts"].is_unique
    assert (df["bar_ts"].diff().dropna() == pd.Timedelta(minutes=15)).all()


def test_unknown_symbol_raises_lookup(clock):
    src = MT5Source(mt5_cfg(), mt5_module=FakeMT5(clock), clock=clock)
    src.connect([V75])
    with pytest.raises(LookupError):
        src.fetch_bars(SymbolSpec("X", "Nope Index", "SYNTHETIC"), datetime(2026, 11, 1))


def test_symbol_specs_and_suggestions(clock):
    src = MT5Source(mt5_cfg(), mt5_module=FakeMT5(clock), clock=clock)
    src.connect([V75])
    assert src.symbol_specs(V75)["volume_min"] == 0.001
    assert src.suggest_names("Volatility") == ["Volatility 75 Index"]
