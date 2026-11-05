"""Null baselines (spec Section 8): a result means nothing until it beats these.

random_entry   Same test windows, same number of signals per fold, same barriers
               and costs — but random times and random direction. Run many times
               to get a distribution; the strategy's percentile in it is the
               honest "is this better than luck?" number.
always_long    The strategy's own entry times, but always long. If this does as
               well, the strategy's direction calls add nothing — it just rode a trend.
buy_and_hold   Price change over the tested period, for context.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import CostModel, ModeProfile
from indicators import atr
from simulator import simulate
from walk_forward import WalkForwardResult


def random_entry(m15: pd.DataFrame, bars: pd.DataFrame, mode: ModeProfile, cost: CostModel,
                 wf: WalkForwardResult, runs: int, seed: int) -> dict:
    rng = np.random.default_rng(seed)
    bar_atr = atr(bars, mode.atr_period).to_numpy()
    close_ts = bars["close_ts"].to_numpy()
    tested = [f for f in wf.folds if f.params is not None]
    per_fold = {f.fold.index: int(((wf.oos_trades["fold"] == f.fold.index)).sum()) for f in tested}
    expectancies = []
    for _ in range(runs):
        trades = []
        for f in tested:
            k = per_fold[f.fold.index]
            pool = np.flatnonzero((close_ts >= np.datetime64(f.fold.test_start)) &
                                  (close_ts < np.datetime64(f.fold.test_end)) & np.isfinite(bar_atr))
            if k == 0 or len(pool) == 0:
                continue
            pick = np.sort(rng.choice(pool, size=min(k, len(pool)), replace=False))
            sig = pd.DataFrame({"signal_ts": close_ts[pick], "direction": rng.choice([-1, 1], size=len(pick)),
                                "atr": bar_atr[pick]})
            trades.append(simulate(m15, sig, mode, cost, (f.fold.test_start, f.fold.test_end)))
        r = pd.concat(trades)["r"] if trades else pd.Series(dtype=float)
        if len(r):
            expectancies.append(float(r.mean()))
    dist = np.array(expectancies)
    strat = wf.oos_trades["r"].mean() if len(wf.oos_trades) else np.nan
    if not len(dist) or not np.isfinite(strat):
        return {"runs": len(dist), "percentile": None}
    return {
        "runs": len(dist),
        "mean_expectancy_r": round(float(dist.mean()), 4),
        "p5_expectancy_r": round(float(np.percentile(dist, 5)), 4),
        "p95_expectancy_r": round(float(np.percentile(dist, 95)), 4),
        "percentile": round(float((dist < strat).mean() * 100), 1),
    }


def always_long(m15: pd.DataFrame, mode: ModeProfile, cost: CostModel, wf: WalkForwardResult) -> dict:
    trades = []
    for f in wf.folds:
        if f.params is None:
            continue
        sig = wf.chosen_signals[f.fold.index].assign(direction=1)
        trades.append(simulate(m15, sig, mode, cost, (f.fold.test_start, f.fold.test_end)))
    r = pd.concat(trades)["r"] if trades else pd.Series(dtype=float)
    return {"trades": int(len(r)), "expectancy_r": round(float(r.mean()), 4) if len(r) else None}


def buy_and_hold(m15: pd.DataFrame, wf: WalkForwardResult) -> dict:
    if wf.oos_start is None:
        return {"return_pct": None}
    seg = m15[(m15["bar_ts"] >= wf.oos_start) & (m15["bar_ts"] < wf.oos_end)]
    if seg.empty:
        return {"return_pct": None}
    px = seg["close_price"].to_numpy()
    dd = (px / np.maximum.accumulate(px) - 1).min() * 100
    return {"return_pct": round(float((px[-1] / seg["open_price"].iloc[0] - 1) * 100), 2),
            "max_drawdown_pct": round(float(-dd), 2)}
