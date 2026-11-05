"""Strategy plugin interface.

A strategy only decides *when* and *which direction*. Entry pricing, stops,
targets, costs and sizing are applied identically to every strategy by the
harness — including the random baseline — so comparisons are fair.
"""
from __future__ import annotations

import itertools
from abc import ABC, abstractmethod

import pandas as pd


class Strategy(ABC):
    name: str = ""
    description: str = ""
    #: parameter name -> candidate values; walk-forward tries every combination
    param_grid: dict[str, list] = {}

    @abstractmethod
    def signals(self, bars: pd.DataFrame, params: dict) -> pd.DataFrame:
        """bars: rolled-up mode bars (bar_ts, close_ts, OHLC). Must be causal.

        Returns columns: signal_ts (a bar's close_ts), direction (+1 long / -1 short).
        """

    def param_sets(self) -> list[dict]:
        keys = list(self.param_grid)
        combos = [dict(zip(keys, vals)) for vals in itertools.product(*(self.param_grid[k] for k in keys))]
        return [p for p in combos if self.valid(p)] or [{}]

    def valid(self, params: dict) -> bool:
        return True
