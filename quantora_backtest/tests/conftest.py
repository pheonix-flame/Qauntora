import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import CostModel, ModeProfile  # noqa: E402


def bars_from(rows, start="2026-01-05 00:00"):
    """rows: list of (open, high, low, close) on consecutive M15 bars."""
    ts = pd.date_range(start, periods=len(rows), freq="15min")
    return pd.DataFrame({"bar_ts": ts, "open_price": [r[0] for r in rows], "high_price": [r[1] for r in rows],
                         "low_price": [r[2] for r in rows], "close_price": [r[3] for r in rows], "volume": 1.0})


@pytest.fixture
def mode():
    return ModeProfile(name="SWING", timeframe="H4", max_hold_minutes=120, atr_period=14, sl_atr=1.0, tp_atr=2.0,
                       cooldown_bars_after_loss=0, train_days=60, test_days=20, embargo_days=1)


@pytest.fixture
def free():
    return CostModel(spread=0.0, slippage=0.0)
