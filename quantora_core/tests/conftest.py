import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import load_config  # noqa: E402


def regime_walk(days=300, end="2026-11-04 12:00", seed=1, vol=0.002, regime_bars=2000, drift=0.0004):
    """M15 random walk with trending regimes (up / flat / down) so tiers actually move."""
    rng = np.random.default_rng(seed)
    n = days * 96
    ts = pd.date_range(end=end, periods=n, freq="15min")
    d = np.repeat(rng.choice([-1, 0, 1], size=n // regime_bars + 1) * drift, regime_bars)[:n]
    close = 1000 * np.exp(np.cumsum(rng.normal(d, vol)))
    o = np.r_[1000, close[:-1]]
    wick = np.abs(rng.normal(0, vol / 2, n)) * close
    return pd.DataFrame({"bar_ts": ts, "open_price": o, "high_price": np.maximum(o, close) + wick,
                         "low_price": np.minimum(o, close) - wick, "close_price": close, "volume": 10.0})


def write_feed_csv(folder: Path, m15: pd.DataFrame, symbol="V75"):
    m15.assign(symbol=symbol, asset_class="SYNTHETIC", timeframe="M15", price_side="BID").to_csv(
        folder / f"ohlcv_{symbol}_M15.csv", index=False)


@pytest.fixture
def cfg(tmp_path):
    from dataclasses import replace
    return replace(load_config(), output_dir=tmp_path / "core_output")
