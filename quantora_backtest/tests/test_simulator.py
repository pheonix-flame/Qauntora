from dataclasses import replace

import pandas as pd
import pytest

from conftest import bars_from
from config import CostModel
from simulator import simulate

T0 = pd.Timestamp("2026-01-05 00:00")


def sig(ts=T0, direction=1, atr=1.0):
    return pd.DataFrame({"signal_ts": [pd.Timestamp(ts)], "direction": [direction], "atr": [atr]})


def flat(n, px=100.0):
    return [(px, px + 0.1, px - 0.1, px)] * n


def test_long_take_profit_fills_at_target_entry_at_next_open(mode, free):
    m15 = bars_from([(100, 100.5, 99.5, 100.2), (100.2, 102.5, 100, 102)] + flat(10, 102))
    t = simulate(m15, sig(), mode, free).iloc[0]
    assert t.entry_ts == T0 and t.entry == 100  # open of the bar starting at the decision time
    assert (t.outcome, t.exit, t.r) == ("WIN", 102, pytest.approx(2.0))


def test_costs_raise_long_entry_and_cut_r(mode):
    m15 = bars_from([(100, 100.5, 99.5, 100.2), (100.2, 103, 100, 102.9)] + flat(10, 103))
    t = simulate(m15, sig(), mode, CostModel(spread=0.2, slippage=0.1)).iloc[0]
    assert t.entry == pytest.approx(100.3)       # ask (bid + spread) + slippage
    assert t.target == pytest.approx(102.3) and t.outcome == "WIN"


def test_stop_loss_includes_slippage(mode):
    m15 = bars_from([(100, 100.2, 98.5, 98.8)] + flat(10, 98.8))
    t = simulate(m15, sig(), mode, CostModel(spread=0.0, slippage=0.1)).iloc[0]
    # entry 100.1 (open + slippage), stop 99.1, exit 99.1 - 0.1 slippage -> R = -1.1
    assert t.outcome == "LOSS" and t.r == pytest.approx(-1.1)


def test_both_barriers_in_one_bar_counts_as_loss(mode, free):
    m15 = bars_from([(100, 103, 98, 101)] + flat(10, 101))
    assert simulate(m15, sig(), mode, free).iloc[0].outcome == "LOSS"


def test_gap_through_stop_fills_at_worse_open(mode, free):
    m15 = bars_from([(100, 100.1, 99.9, 100), (97, 97.2, 96.8, 97)] + flat(10, 97))
    t = simulate(m15, sig(), mode, free).iloc[0]
    assert t.exit == 97 and t.r == pytest.approx(-3.0)


def test_short_stopped_by_spread_alone(mode):
    # bid high 100.9 never reaches the 101.0 stop, but the ask (bid + 0.2) does
    m15 = bars_from([(100, 100.9, 99.5, 100.5)] + flat(10, 100.5))
    t = simulate(m15, sig(direction=-1), mode, CostModel(spread=0.2)).iloc[0]
    assert t.outcome == "LOSS"


def test_timeout_exits_at_next_available_open(mode, free):
    m15 = bars_from(flat(20))
    t = simulate(m15, sig(), mode, free).iloc[0]
    assert t.outcome == "TIMEOUT" and t.exit_ts == T0 + pd.Timedelta(minutes=120) and t.r == pytest.approx(0)


def test_timeout_across_a_gap_takes_gap_risk(mode, free):
    before = bars_from(flat(4))                                  # 00:00–01:00
    after = bars_from([(99.5, 99.6, 99.4, 99.5)] * 8, start="2026-01-05 06:00")  # reopens lower at 06:00
    t = simulate(pd.concat([before, after], ignore_index=True), sig(), mode, free).iloc[0]
    assert t.outcome == "TIMEOUT" and t.exit_ts == pd.Timestamp("2026-01-05 06:00") and t.exit == 99.5


def test_unresolved_trade_at_end_of_data_is_dropped(mode, free):
    assert simulate(bars_from(flat(4)), sig(), mode, free).empty


def test_one_position_at_a_time_and_cooldown(mode, free):
    m15 = bars_from([(100, 100.1, 98.9, 99)] + flat(30, 99))
    two = pd.concat([sig(T0), sig(T0 + pd.Timedelta(minutes=30))], ignore_index=True)
    assert len(simulate(m15, two, mode, free)) == 2  # first one stopped out in bar 0, so the second is allowed
    cooled = replace(mode, cooldown_bars_after_loss=1)  # 1 x H4 bar
    assert len(simulate(m15, two, cooled, free)) == 1


def test_swap_reduces_r():
    from config import ModeProfile
    long_mode = ModeProfile("SWING", "H4", 3 * 1440, 14, 1.0, 50.0, 0, 60, 20, 1)
    m15 = bars_from(flat(3 * 96 + 8))
    no_swap = simulate(m15, sig(), long_mode, CostModel(0.0)).iloc[0]
    swap = simulate(m15, sig(), long_mode, CostModel(0.0, swap_long_per_day=0.1)).iloc[0]
    assert swap.r == pytest.approx(no_swap.r - 0.3)
