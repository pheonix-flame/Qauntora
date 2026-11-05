from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

import baselines
import gate
import metrics
import walk_forward
from config import CostModel, GateConfig, MissingCostError, load_config, parse_settings
from runner import run_backtest
from strategies import Strategy, get_strategy
from synthetic import random_walk
from timeframes import rollup


# -- timeframes -----------------------------------------------------------------------
def test_rollup_alignment_and_values():
    m15 = random_walk(days=10, start="2026-01-07 00:00", seed=1)  # a Wednesday
    h4 = rollup(m15, "H4")
    assert set(h4["bar_ts"].dt.hour) == {0, 4, 8, 12, 16, 20}
    first = m15[m15["bar_ts"] < "2026-01-07 04:00"]
    row = h4.iloc[0]
    assert (row.open_price, row.close_price) == (first.open_price.iloc[0], first.close_price.iloc[-1])
    assert row.high_price == first.high_price.max() and row.bars == 16
    assert row.close_ts == pd.Timestamp("2026-01-07 04:00")
    assert (rollup(m15, "D1")["bar_ts"].dt.hour == 0).all()
    w1 = rollup(m15, "W1")
    assert w1["bar_ts"].iloc[0] == pd.Timestamp("2026-01-05") and (w1["bar_ts"].dt.dayofweek == 0).all()


# -- walk-forward -----------------------------------------------------------------------
def test_folds_are_sequential_with_embargo(mode):
    folds = walk_forward.make_folds(pd.Timestamp("2025-01-01"), pd.Timestamp("2025-12-31"), mode)
    assert len(folds) >= 10
    for f in folds:
        assert f.test_start - f.train_end == pd.Timedelta(days=mode.embargo_days)
        assert f.train_end - f.train_start == pd.Timedelta(days=mode.train_days)
    for a, b in zip(folds, folds[1:]):
        assert b.test_start == a.test_end  # test windows tile the period without overlap


def test_purge_drops_training_trades_that_resolve_in_test():
    trades = pd.DataFrame({"exit_ts": pd.to_datetime(["2025-03-01", "2025-03-09", "2025-03-11"]), "r": [1, 1, 1]})
    kept = walk_forward.purge(trades, pd.Timestamp("2025-03-10"))
    assert list(kept["exit_ts"]) == list(pd.to_datetime(["2025-03-01", "2025-03-09"]))


class Peek(Strategy):
    """CHEATS: looks `ahead` bars into the future. Only for testing the harness."""
    name = "peek"
    param_grid = {"ahead": [6]}

    def signals(self, bars, params):
        fut = bars["close_price"].shift(-params["ahead"]) - bars["close_price"]
        pick = fut.abs() > fut.abs().quantile(0.8)
        return pd.DataFrame({"signal_ts": bars.loc[pick, "close_ts"],
                             "direction": np.sign(fut[pick]).astype(int)}).reset_index(drop=True)


def cfg_with(cost=CostModel(spread=0.05, slippage=0.02), runs=40):
    base = load_config()
    return replace(base, costs={"SYN": cost}, random_runs=runs)


def test_harness_finds_no_edge_in_a_random_walk():
    rep = run_backtest(cfg_with(), random_walk(days=730, seed=3), "SYN", "SWING", get_strategy("ema_cross"))
    assert rep.oos_metrics["trades"] >= 30
    assert not rep.passed
    assert rep.random["percentile"] < 95


def test_harness_detects_a_real_edge():
    rep = run_backtest(cfg_with(), random_walk(days=730, seed=3), "SYN", "SWING", Peek())
    assert rep.oos_metrics["expectancy_r"] > 0.5
    assert rep.random["percentile"] >= 95
    assert rep.passed


def test_parameters_are_chosen_on_training_data_only(mode, free):
    """Changing prices inside a test window must not change the params picked for that fold."""
    m15 = random_walk(days=200, seed=5)
    bars = rollup(m15, mode.timeframe)
    strat = get_strategy("ema_cross")
    a = walk_forward.run(strat, m15, bars, replace(mode, max_hold_minutes=2880), free, min_train_trades=1)
    fold = next(f for f in a.folds if f.params is not None)
    tampered = m15.copy()
    in_test = (tampered["bar_ts"] >= fold.fold.test_start) & (tampered["bar_ts"] < fold.fold.test_end)
    tampered.loc[in_test, ["open_price", "high_price", "low_price", "close_price"]] *= 1.5
    b = walk_forward.run(strat, tampered, rollup(tampered, mode.timeframe), replace(mode, max_hold_minutes=2880),
                         free, min_train_trades=1)
    assert next(f for f in b.folds if f.fold.index == fold.fold.index).params == fold.params


def test_configs_evaluated_counts_every_try(mode, free):
    m15 = random_walk(days=200, seed=5)
    wf = walk_forward.run(get_strategy("ema_cross"), m15, rollup(m15, "H4"), mode, free, min_train_trades=1)
    combos = len(get_strategy("ema_cross").param_sets())
    assert combos == 9  # every fast value is below every slow value
    assert wf.configs_evaluated == len(wf.folds) * combos


# -- metrics, baselines, gate --------------------------------------------------------------
def test_metrics_known_values():
    t = pd.DataFrame({"r": [2.0, -1.0, -1.0, 2.0], "outcome": ["WIN", "LOSS", "LOSS", "WIN"],
                      "minutes_held": [60] * 4})
    m = metrics.compute(t, risk_pct=1.0, period_days=365.25)
    assert m["win_rate"] == 0.5 and m["expectancy_r"] == 0.5 and m["profit_factor"] == 2.0
    eq = np.cumprod([1.02, 0.99, 0.99, 1.02])
    assert m["max_drawdown_pct"] == pytest.approx((1 - eq[2] / eq[0]) * 100, abs=1e-3)
    assert m["trades_per_year"] == 4


def test_random_baseline_is_reproducible():
    cfg = cfg_with(runs=20)
    m15 = random_walk(days=500, seed=9)
    a = run_backtest(cfg, m15, "SYN", "SWING", get_strategy("ema_cross"), seed=1).random
    b = run_backtest(cfg, m15, "SYN", "SWING", get_strategy("ema_cross"), seed=1).random
    assert a == b and 0 <= a["percentile"] <= 100


def test_gate_rules():
    good = {"trades": 40, "expectancy_r": 0.3, "max_drawdown_pct": 8.0}
    rules = gate.evaluate(good, {"percentile": 99.0}, GateConfig())
    assert gate.verdict(rules) and rules[-1].passed is None  # Tier-2-vs-3 not checkable yet
    assert not gate.verdict(gate.evaluate(good | {"trades": 12}, {"percentile": 99.0}, GateConfig()))
    assert not gate.verdict(gate.evaluate(good, {"percentile": 60.0}, GateConfig()))


# -- config -------------------------------------------------------------------------------
def test_shipped_settings_load_and_require_costs():
    cfg = load_config()
    assert set(cfg.modes) == {"SCALP", "SWING", "LONG"}
    assert cfg.modes["SWING"].timeframe == "H4" and cfg.modes["SCALP"].max_hold_minutes == 45
    with pytest.raises(MissingCostError, match="quantora_feed"):
        cfg.cost_for("V75")


def test_bad_settings_rejected(tmp_path):
    with pytest.raises(ValueError):
        parse_settings({"modes": {"SWING": {"timeframe": "H3"}}}, tmp_path / "s.toml")
    with pytest.raises(ValueError):
        parse_settings({"costs": {"V75": {"spread": -1}}}, tmp_path / "s.toml")
