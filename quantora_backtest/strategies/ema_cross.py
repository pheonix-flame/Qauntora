"""EMA crossover — a REFERENCE strategy for exercising the harness.

It is NOT Quantora's strategy. Quantora Core (build step 3) brings the real
three-tier confluence logic. This exists so the walk-forward, costs and
baselines can be run end-to-end today, and as a simple trend-following
benchmark Core will later have to beat.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from indicators import ema
from strategies.base import Strategy


class EmaCross(Strategy):
    name = "ema_cross"
    description = "Fast/slow EMA crossover on closed bars (harness reference, not Quantora's strategy)"
    param_grid = {"fast": [10, 20, 30], "slow": [50, 100, 200]}

    def valid(self, params: dict) -> bool:
        return params["fast"] < params["slow"]

    def signals(self, bars: pd.DataFrame, params: dict) -> pd.DataFrame:
        fast = ema(bars["close_price"], params["fast"])
        slow = ema(bars["close_price"], params["slow"])
        side = np.sign(fast - slow)
        cross = (side != side.shift(1)) & side.ne(0) & side.shift(1).notna() & slow.notna()
        out = pd.DataFrame({"signal_ts": bars.loc[cross, "close_ts"], "direction": side[cross].astype(int)})
        return out.reset_index(drop=True)
