"""Indicators, confluence tiers, news blackout, state machine — and no look-ahead."""
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

import indicators as ind
from config import validate_signal_config
from confluence import describe, evaluate, missing_items, suggestion
from conftest import regime_walk
from news_filter import apply_blackout
from state_machine import is_upgrade_entry, state_key, transitions
from timeframes import rollup


def h4(seed=1, days=300):
    return rollup(regime_walk(days=days, seed=seed), "H4")


# -- indicators ------------------------------------------------------------------------
def test_rsi_edges_and_range():
    up = pd.Series(np.arange(100, 160, dtype=float))
    assert ind.rsi(up).dropna().eq(100).all()
    assert ind.rsi(pd.Series([5.0] * 40)).dropna().eq(50).all()
    r = ind.rsi(pd.Series(regime_walk(days=20)["close_price"].to_numpy())).dropna()
    assert r.between(0, 100).all()


def test_adx_bollinger_atr_ranges():
    bars = h4()
    a = ind.adx(bars).dropna()
    assert a["adx"].between(0, 100).all() and (a["plus_di"] >= 0).all()
    assert (ind.atr(bars).dropna() > 0).all()
    bb = ind.bollinger(bars["close_price"]).dropna()
    assert (bb["upper"] >= bb["lower"]).all()


@pytest.mark.parametrize("fn", [lambda b: ind.ema(b["close_price"], 50), lambda b: ind.rsi(b["close_price"]),
                                lambda b: ind.adx(b)["adx"], lambda b: ind.bollinger(b["close_price"])["pct_b"],
                                lambda b: ind.atr(b), lambda b: ind.obv(b)])
def test_indicators_are_causal(fn):
    bars = h4()
    full, cut = fn(bars), fn(bars.iloc[:900])
    pd.testing.assert_series_equal(full.iloc[:900], cut, check_names=False)


# -- confluence ---------------------------------------------------------------------------
def test_tiers_appear_and_respect_thresholds(cfg):
    ev = evaluate(h4(), cfg.defaults, cfg.modes["SWING"], "SYNTHETIC")
    counts = ev["tier"].value_counts()
    assert {"WATCHING", "FORMING", "CONFIRMED"} <= set(counts.index)
    conf = ev[ev["tier"] == "CONFIRMED"]
    assert (conf["score"] >= cfg.defaults.tier3_threshold - 1e-9).all()
    assert conf["trigger"].all() and not conf["chase"].any() and conf["warm"].all()
    form = ev[ev["tier"] == "FORMING"]
    assert (form["score"] >= cfg.defaults.tier2_threshold - 1e-9).all()
    assert not ev.loc[~ev["warm"], "tier"].ne("WATCHING").any()  # warm-up never signals


def test_confirmed_direction_matches_trend_and_trigger(cfg):
    ev = evaluate(h4(), cfg.defaults, cfg.modes["SWING"], "SYNTHETIC")
    conf = ev[ev["tier"] == "CONFIRMED"]
    buys, sells = conf[conf["direction"] == 1], conf[conf["direction"] == -1]
    assert (buys["ema_fast"] > buys["ema_slow"]).all() and (buys["close"] > buys["open"]).all()
    assert (sells["ema_fast"] < sells["ema_slow"]).all() and (sells["close"] < sells["open"]).all()


def test_no_lookahead_in_tiers(cfg):
    """Evaluations up to bar k must not change when later bars exist."""
    bars = h4()
    full = evaluate(bars, cfg.defaults, cfg.modes["SWING"], "SYNTHETIC")
    for k in (400, 900, 1500):
        cut = evaluate(bars.iloc[:k], cfg.defaults, cfg.modes["SWING"], "SYNTHETIC")
        assert list(full["tier"].iloc[:k]) == list(cut["tier"])
        np.testing.assert_allclose(full["score"].iloc[:k], cut["score"])


def test_volume_condition_only_for_crypto(cfg):
    syn = evaluate(h4(), cfg.defaults, cfg.modes["SWING"], "SYNTHETIC")
    cry = evaluate(h4(), cfg.defaults, cfg.modes["SWING"], "CRYPTO")
    assert "volume" not in syn.attrs["active_conditions"] and "volume" in cry.attrs["active_conditions"]
    assert "obv_slope" in cry and "obv_slope" not in syn


def test_macd_replaces_rsi(cfg):
    macd_cfg = validate_signal_config(0.6, 0.8, {}, {"momentum": "MACD"}, base=cfg.defaults)
    ev = evaluate(h4(), macd_cfg, cfg.modes["SWING"], "SYNTHETIC")
    assert "macd_hist" in ev and "rsi" not in ev


def test_chase_guard_blocks_confirmation(cfg):
    tight = validate_signal_config(0.6, 0.8, {}, {"chase_atr": 0.01}, base=cfg.defaults)
    ev = evaluate(h4(), tight, cfg.modes["SWING"], "SYNTHETIC")
    assert not (ev["tier"] == "CONFIRMED").any()


def test_forming_notes_say_what_is_missing(cfg):
    ev = evaluate(h4(), cfg.defaults, cfg.modes["SWING"], "SYNTHETIC")
    active = ev.attrs["active_conditions"]
    no_trigger = ev[(ev["tier"] == "FORMING") & ~ev["trigger"]].iloc[0]
    items = missing_items(no_trigger, active, cfg.defaults)
    assert any("confirmed" in i and "close" in i for i in items)
    note = describe(no_trigger, "V75", cfg.modes["SWING"], cfg.defaults, active)
    assert note.startswith("V75 SWING: Setup forming") and "No trade yet" in note and len(note) <= 400


def test_confirmed_suggestion_uses_atr_barriers(cfg):
    ev = evaluate(h4(), cfg.defaults, cfg.modes["SWING"], "SYNTHETIC")
    row = ev[ev["tier"] == "CONFIRMED"].iloc[0]
    s, m, d = suggestion(row, cfg.modes["SWING"]), cfg.modes["SWING"], int(row["direction"])
    assert s["entry"] == row["close"]
    assert s["stop"] == pytest.approx(row["close"] - d * m.sl_atr * row["atr"])
    assert s["target"] == pytest.approx(row["close"] + d * m.tp_atr * row["atr"])


# -- news ---------------------------------------------------------------------------------
def test_news_blackout_downgrades_only_inside_window(cfg):
    ev = evaluate(h4(), cfg.defaults, cfg.modes["SWING"], "FOREX")
    conf = ev[ev["tier"] == "CONFIRMED"]
    hit, miss = conf.iloc[0], conf.iloc[-1]
    events = pd.DataFrame({"event_ts": [hit["close_ts"] + pd.Timedelta(minutes=20),
                                        miss["close_ts"] + pd.Timedelta(minutes=90)],
                           "currency": ["USD", "USD"], "impact": ["HIGH", "HIGH"], "title": ["CPI", "NFP"]})
    out = apply_blackout(ev, events, ["EUR", "USD"], before_min=30, after_min=15)
    assert out.loc[hit.name, "tier"] == "FORMING" and "CPI" in out.loc[hit.name, "blackout"]
    assert out.loc[miss.name, "tier"] == "CONFIRMED"
    low = events.assign(impact="LOW")
    assert (apply_blackout(ev, low, ["USD"], 30, 15)["tier"] == ev["tier"]).all()
    assert (apply_blackout(ev, events, ["JPY"], 30, 15)["tier"] == ev["tier"]).all()


# -- state machine ---------------------------------------------------------------------------
def test_transitions_log_changes_only():
    ev = pd.DataFrame({"tier": ["WATCHING", "WATCHING", "FORMING", "FORMING", "CONFIRMED", "CONFIRMED",
                                "FORMING", "WATCHING", "WATCHING"],
                       "direction": [1, -1, 1, 1, 1, 1, 1, -1, 1]})
    tr = transitions(ev)
    assert list(tr["tier"]) == ["FORMING", "CONFIRMED", "FORMING", "WATCHING"]
    assert list(tr["prev_tier"]) == ["WATCHING", "FORMING", "CONFIRMED", "FORMING"]
    assert transitions(ev.iloc[2:], start_key=("FORMING", "BUY"))["tier"].iloc[0] == "CONFIRMED"


def test_direction_flip_is_a_transition_and_entries():
    ev = pd.DataFrame({"tier": ["CONFIRMED", "CONFIRMED"], "direction": [1, -1]})
    assert len(transitions(ev)) == 2
    assert state_key("WATCHING", 1) == state_key("WATCHING", "SELL")
    assert is_upgrade_entry("CONFIRMED", "FORMING", "BUY", "BUY")
    assert not is_upgrade_entry("FORMING", "CONFIRMED", "BUY", "BUY")      # decay isn't an entry
    assert is_upgrade_entry("FORMING", "FORMING", "BUY", "SELL")           # flip is


# -- config --------------------------------------------------------------------------------
def test_config_validation(cfg):
    with pytest.raises(ValueError):
        validate_signal_config(0.9, 0.8, {}, {}, base=cfg.defaults)
    with pytest.raises(ValueError, match="Unknown"):
        validate_signal_config(0.6, 0.8, {"vibes": 1}, {}, base=cfg.defaults)
    with pytest.raises(ValueError):
        validate_signal_config(0.6, 0.8, {}, {"ema_fast": 60}, base=cfg.defaults)
    merged = validate_signal_config(0.5, 0.7, {"location": 0.0}, {"adx_min": "25"}, version_id=3, base=cfg.defaults)
    assert merged.params["adx_min"] == 25.0 and merged.weights["trend"] == 1.0 and merged.source == \
        "approved config v3"


def test_shipped_settings(cfg):
    assert cfg.pairs() == [("V75", "SWING")]
    assert cfg.modes["SWING"].timeframe == "H4" and cfg.asset("BTCUSD") == "CRYPTO"
    assert cfg.asset("UNKNOWN") == "SYNTHETIC" and cfg.news_currencies["XAUUSD"] == ["USD"]


def test_rollup_matches_documented_alignment():
    m15 = regime_walk(days=10, end="2026-01-16 23:45")
    assert set(rollup(m15, "H4")["bar_ts"].dt.hour) == {0, 4, 8, 12, 16, 20}
    assert (rollup(m15, "W1")["bar_ts"].dt.dayofweek == 0).all()
