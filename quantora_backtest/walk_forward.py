"""Purged walk-forward with embargo (spec Section 8).

Timeline per fold (rolling):

    |---- train ----|embargo|-- test --|
                            |---- train ----|embargo|-- test --|   (next fold, shifted by test_days)

* Parameters are chosen on TRAIN only (best expectancy), then frozen and run
  on TEST. Only TEST trades count toward the reported results.
* PURGE: any training trade whose outcome window reaches into the test
  period (exit_ts >= test_start) is dropped — its label would leak test data.
* EMBARGO: a gap between train end and test start, so the last training
  decisions and the first test decisions don't share price action.
* Every parameter set tried is counted (configs_evaluated) — the overfitting
  guard: "looks great" should be weighed against "how many tries did it take".
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from config import CostModel, ModeProfile
from indicators import atr
from simulator import TRADE_COLUMNS, simulate
from strategies import Strategy

MIN_TRAIN_TRADES = 10


@dataclass(frozen=True)
class Fold:
    index: int
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp


@dataclass
class FoldResult:
    fold: Fold
    params: dict | None
    train_trades: int
    train_expectancy_r: float | None
    test_trades: int
    test_expectancy_r: float | None
    note: str = ""


@dataclass
class WalkForwardResult:
    oos_trades: pd.DataFrame
    folds: list[FoldResult]
    configs_evaluated: int
    oos_start: pd.Timestamp | None
    oos_end: pd.Timestamp | None
    chosen_signals: dict[int, pd.DataFrame] = field(default_factory=dict)  # fold -> signals used in test

    @property
    def oos_days(self) -> float:
        if self.oos_start is None:
            return 0.0
        return (self.oos_end - self.oos_start) / pd.Timedelta(days=1)


def make_folds(data_start: pd.Timestamp, data_end: pd.Timestamp, mode: ModeProfile) -> list[Fold]:
    train, test, emb = (pd.Timedelta(days=d) for d in (mode.train_days, mode.test_days, mode.embargo_days))
    folds, t0 = [], pd.Timestamp(data_start)
    while True:
        tr_end = t0 + train
        te_start = tr_end + emb
        te_end = min(te_start + test, pd.Timestamp(data_end))
        if te_end - te_start < test / 2:  # allow a final partial fold if it's at least half a test window
            break
        folds.append(Fold(len(folds), t0, tr_end, te_start, te_end))
        if te_end >= data_end:
            break
        t0 = t0 + test
    return folds


def attach_atr(signals: pd.DataFrame, bars: pd.DataFrame, period: int) -> pd.DataFrame:
    """ATR as known at the signal bar's close (same bar the decision was made on)."""
    a = pd.Series(atr(bars, period).to_numpy(), index=bars["close_ts"])
    return signals.assign(atr=signals["signal_ts"].map(a)).dropna(subset=["atr"])


def purge(trades: pd.DataFrame, test_start: pd.Timestamp) -> pd.DataFrame:
    return trades[trades["exit_ts"] < test_start]


def run(strategy: Strategy, m15: pd.DataFrame, bars: pd.DataFrame, mode: ModeProfile,
        cost: CostModel, min_train_trades: int = MIN_TRAIN_TRADES) -> WalkForwardResult:
    folds = make_folds(bars["bar_ts"].iloc[0], m15["bar_ts"].iloc[-1] + pd.Timedelta(minutes=15), mode)
    param_sets = strategy.param_sets()
    signals = {i: attach_atr(strategy.signals(bars, p), bars, mode.atr_period) for i, p in enumerate(param_sets)}

    results, oos, chosen = [], [], {}
    for fold in folds:
        best, best_score, best_n = None, -np.inf, 0
        for i in range(len(param_sets)):
            tr = purge(simulate(m15, signals[i], mode, cost, (fold.train_start, fold.train_end)), fold.test_start)
            if len(tr) >= min_train_trades and tr["r"].mean() > best_score:
                best, best_score, best_n = i, float(tr["r"].mean()), len(tr)
        if best is None:
            results.append(FoldResult(fold, None, 0, None, 0, None,
                                      f"no parameter set produced {min_train_trades}+ training trades — fold skipped"))
            continue
        te = simulate(m15, signals[best], mode, cost, (fold.test_start, fold.test_end))
        chosen[fold.index] = signals[best]
        oos.append(te.assign(fold=fold.index))
        results.append(FoldResult(fold, param_sets[best], best_n, best_score, len(te),
                                  float(te["r"].mean()) if len(te) else None))

    oos_trades = pd.concat(oos, ignore_index=True) if oos else pd.DataFrame(columns=TRADE_COLUMNS + ["fold"])
    tested = [r.fold for r in results if r.params is not None]
    return WalkForwardResult(
        oos_trades=oos_trades, folds=results, configs_evaluated=len(folds) * len(param_sets),
        oos_start=tested[0].test_start if tested else None, oos_end=tested[-1].test_end if tested else None,
        chosen_signals=chosen)
