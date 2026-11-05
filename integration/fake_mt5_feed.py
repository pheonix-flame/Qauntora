"""Run Quantora Feed's REAL command line against a simulated MT5 terminal.

For integration testing only (MT5 is Windows-only and needs a live broker login).
Everything after the MT5 boundary — Feed's code, the database, every other
engine — is the real thing.

    python integration/fake_mt5_feed.py backfill --days 400 --dry-run

The simulated market is a regime-switching random walk (up-trends, down-trends,
chop), deterministic per bar so every process sees identical prices.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

FEED = Path(__file__).resolve().parents[1] / "quantora_feed"
sys.path.insert(0, str(FEED))
sys.path.insert(0, str(FEED / "tests"))

from conftest import FakeMT5  # noqa: E402  (Feed's own test double)

N0 = int(np.datetime64("2021-01-01T00:00", "s").astype("int64")) // 900   # first simulated bar
N1 = int(np.datetime64("2028-01-01T00:00", "s").astype("int64")) // 900


def _build_market(seed: int = 7, start_price: float = 400_000.0):
    rng = np.random.default_rng(seed)
    n = N1 - N0
    regime_len = 1200
    drift = np.repeat(rng.choice([-1, 0, 0, 1], size=n // regime_len + 1) * 0.00035, regime_len)[:n]
    close = start_price * np.exp(np.cumsum(rng.normal(drift, 0.0018)))
    open_ = np.r_[start_price, close[:-1]]
    wick = np.abs(rng.normal(0, 0.0008, n)) * close
    return open_, np.maximum(open_, close) + wick, np.minimum(open_, close) - wick, close


_O, _H, _L, _C = _build_market()


class MarketSim(FakeMT5):
    def m15_prices(self, times: np.ndarray):
        idx = np.clip(times // 900 - N0, 0, len(_C) - 1)
        return _O[idx], _H[idx], _L[idx], _C[idx]


if __name__ == "__main__":
    sys.modules["MetaTrader5"] = MarketSim(time.time, offset_s=0, symbols=("Volatility 75 Index", "BTCUSD"))
    import main as feed_main  # Feed's real CLI
    sys.exit(feed_main.main(sys.argv[1:]))
