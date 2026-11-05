"""Backtesting Quantora Core's replay file (data handed over between engines)."""
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

import gate
from config import CostModel, load_config
from runner import run_backtest
from strategies import get_strategy
from synthetic import random_walk
from timeframes import rollup


def replay_file(tmp_path, m15, symbol="V75", mode="SWING", every=12, seed=0):
    """A file in exactly the format `python main.py replay` (quantora_core) writes."""
    bars = rollup(m15, "H4")
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(60, len(bars) - 1, every):
        d = rng.choice(["BUY", "SELL"])
        ts = bars["close_ts"].iloc[i]
        rows.append({"signal_ts": ts, "bar_ts": bars["bar_ts"].iloc[i], "symbol": symbol, "mode": mode,
                     "tier": "FORMING", "prev_tier": "WATCHING", "direction": d, "prev_direction": None,
                     "is_entry": True, "score": 0.75, "entry": None, "stop": None, "target": None, "notes": "x"})
        ts2 = bars["close_ts"].iloc[i + 1]
        rows.append({"signal_ts": ts2, "bar_ts": bars["bar_ts"].iloc[i + 1], "symbol": symbol, "mode": mode,
                     "tier": "CONFIRMED", "prev_tier": "FORMING", "direction": d, "prev_direction": d,
                     "is_entry": True, "score": 1.0, "entry": 1.0, "stop": 0.9, "target": 1.2, "notes": "x"})
        rows.append({"signal_ts": ts2 + pd.Timedelta(hours=8), "bar_ts": bars["bar_ts"].iloc[i + 1], "symbol": symbol,
                     "mode": mode, "tier": "FORMING", "prev_tier": "CONFIRMED", "direction": d, "prev_direction": d,
                     "is_entry": False, "score": 0.75, "entry": None, "stop": None, "target": None, "notes": "decay"})
    path = tmp_path / f"replay_{symbol}_{mode}.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def test_reads_only_entries_of_the_requested_tier(tmp_path):
    m15 = random_walk(days=200, seed=4)
    path = replay_file(tmp_path, m15)
    s3 = get_strategy("core_signals", path=path, symbol="V75", mode="SWING")
    s2 = s3.tier_variant()
    sig3, sig2 = s3.signals(None, {}), s2.signals(None, {})
    raw = pd.read_csv(path)
    assert len(sig3) == ((raw["tier"] == "CONFIRMED") & raw["is_entry"]).sum()
    assert len(sig2) == ((raw["tier"] == "FORMING") & raw["is_entry"]).sum()  # decay rows excluded
    assert set(sig3["direction"]) <= {1, -1}


def test_rejects_wrong_symbol_or_file(tmp_path):
    path = replay_file(tmp_path, random_walk(days=60, seed=4))
    with pytest.raises(ValueError, match="EURUSD"):
        get_strategy("core_signals", path=path, symbol="EURUSD", mode="SWING")
    with pytest.raises(ValueError, match="needs --signals"):
        get_strategy("core_signals", path=None, symbol="V75", mode="SWING")
    bad = tmp_path / "bad.csv"
    pd.DataFrame({"a": [1]}).to_csv(bad, index=False)
    with pytest.raises(ValueError, match="isn't a Core replay file"):
        get_strategy("core_signals", path=bad, symbol="V75", mode="SWING")


def test_backtest_of_core_signals_fills_the_tier_rule(tmp_path):
    m15 = random_walk(days=730, seed=6)
    cfg = replace(load_config(), costs={"V75": CostModel(spread=0.05, slippage=0.02)}, random_runs=20)
    strat = get_strategy("core_signals", path=replay_file(tmp_path, m15), symbol="V75", mode="SWING")
    rep = run_backtest(cfg, m15, "V75", "SWING", strat)
    assert all(f.params == {} for f in rep.wf.folds)               # fixed config: every fold tested
    assert rep.wf.configs_evaluated == len(rep.wf.folds)
    assert rep.tiers["tier2_trades"] > 0 and rep.tiers["tier3_trades"] > 0
    tier = [r for r in rep.rules if r.name == "Tier 3 beats Tier 2"][0]
    assert tier.passed is not None and "Tier 3" in tier.detail
    assert rep.to_json()["tier3_vs_tier2"] == rep.tiers


def test_tier_rule_logic():
    assert gate.tier_rule({"tier3_expectancy_r": 0.3, "tier2_expectancy_r": 0.1, "tier2_trades": 40}).passed
    assert not gate.tier_rule({"tier3_expectancy_r": 0.1, "tier2_expectancy_r": 0.3, "tier2_trades": 40}).passed
    assert gate.tier_rule({"tier3_expectancy_r": 0.1, "tier2_expectancy_r": None, "tier2_trades": 0}).passed is False
    assert gate.tier_rule(None).passed is None
