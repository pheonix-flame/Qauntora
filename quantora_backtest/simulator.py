"""Triple-barrier trade simulation on the M15 price path (spec Sections 6 & 12).

For each signal:
  entry      the OPEN of the first M15 bar at/after the signal bar's close_ts —
             a decision made on a closed bar can't fill at that bar's prices.
  barriers   SL / TP at sl_atr / tp_atr x ATR from the fill; vertical barrier at
             max_hold_minutes (exit at market on the last fully-closed M15 bar).
  timeout    at the open of the first bar at/after the deadline (after a weekend
             gap that's the reopen price — gap risk included, not skipped).
  path       walked on M15 bars even for H4/D1 signals, so we know which barrier
             was touched first far more precisely than with the signal bars.

Pricing (stored bars are BID):
  long   buy at ask = bid + spread (+ slippage); exits sell at bid.
  short  sell at bid (- slippage); exits buy back at ask = bid + spread.
  SL fills at the stop (or the bar's open if price gapped through it) minus
  slippage; TP is a limit order and fills exactly at TP.

Pessimistic by design:
  * if SL and TP are both inside one M15 bar, assume SL was hit first;
  * gaps through the stop fill at the worse open price, not the stop.

One position per symbol/mode at a time; optional cooldown after a loss.
R-multiple = profit / initial risk (fill-to-stop distance), after swap.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import CostModel, ModeProfile

TRADE_COLUMNS = ["signal_ts", "entry_ts", "direction", "atr", "entry", "stop", "target",
                 "exit_ts", "exit", "outcome", "r", "minutes_held"]


def simulate(m15: pd.DataFrame, signals: pd.DataFrame, mode: ModeProfile, cost: CostModel,
             window: tuple[pd.Timestamp, pd.Timestamp] | None = None) -> pd.DataFrame:
    """signals: columns signal_ts (decision time = a bar's close_ts), direction (+1/-1), atr.

    `window` keeps only entries with window[0] <= entry_ts < window[1].
    Trades that haven't resolved by the end of the data are dropped (never guessed).
    """
    if signals.empty or m15.empty:
        return pd.DataFrame(columns=TRADE_COLUMNS)
    ts = m15["bar_ts"].to_numpy()
    o = m15["open_price"].to_numpy(float)
    h = m15["high_price"].to_numpy(float)
    lo = m15["low_price"].to_numpy(float)
    bar = np.timedelta64(15, "m")
    max_hold = np.timedelta64(mode.max_hold_minutes, "m")
    cooldown = np.timedelta64(mode.cooldown_bars_after_loss * mode.bar_minutes, "m")
    spread, slip = cost.spread, cost.slippage

    rows = []
    busy_until = np.datetime64("NaT", "ns")
    last_loss_exit = np.datetime64("NaT", "ns")
    for sig in signals.sort_values("signal_ts").itertuples(index=False):
        d, atr_val = int(sig.direction), float(sig.atr)
        if d not in (1, -1) or not np.isfinite(atr_val) or atr_val <= 0:
            continue
        sig_ts = np.datetime64(pd.Timestamp(sig.signal_ts))
        i = int(np.searchsorted(ts, sig_ts, side="left"))
        if i >= len(ts):
            continue
        entry_ts = ts[i]
        if window is not None and not (np.datetime64(window[0]) <= entry_ts < np.datetime64(window[1])):
            continue
        if not np.isnat(busy_until) and entry_ts < busy_until:
            continue  # already in a position
        if not np.isnat(last_loss_exit) and entry_ts < last_loss_exit + cooldown:
            continue  # cooling down after a loss
        if entry_ts - sig_ts > max_hold:
            continue  # data gap: the market moved on long before we could have entered

        if d == 1:
            entry = o[i] + spread + slip
        else:
            entry = o[i] - slip
        risk = mode.sl_atr * atr_val
        stop, target = entry - d * risk, entry + d * mode.tp_atr * atr_val

        deadline = entry_ts + max_hold
        j_end = int(np.searchsorted(ts, deadline, side="left"))  # bars that start before the deadline
        path = slice(i, j_end)
        if d == 1:
            hit_sl = lo[path] <= stop
            hit_tp = h[path] >= target
        else:
            hit_sl = h[path] + spread >= stop
            hit_tp = lo[path] + spread <= target
        k_sl = int(np.argmax(hit_sl)) if hit_sl.any() else None
        k_tp = int(np.argmax(hit_tp)) if hit_tp.any() else None

        if k_sl is not None and (k_tp is None or k_sl <= k_tp):  # same bar -> SL (pessimistic)
            k = i + k_sl
            if d == 1:
                exit_px = min(stop, o[k]) - slip
            else:
                exit_px = max(stop, o[k] + spread) + slip
            outcome = "LOSS"
        elif k_tp is not None:
            k = i + k_tp
            exit_px, outcome = target, "WIN"
        else:
            # Vertical barrier: exit at the first price available at/after the deadline —
            # after a weekend/maintenance gap that's the reopen, gap risk included.
            if j_end >= len(ts):
                continue  # data ends before the deadline: unresolved, don't guess
            k = j_end
            exit_px = o[k] - slip if d == 1 else o[k] + spread + slip
            outcome = "TIMEOUT"
        exit_ts = ts[k] if outcome == "TIMEOUT" else ts[k] + bar
        days = (exit_ts - entry_ts) / np.timedelta64(1, "D")
        swap = (cost.swap_long_per_day if d == 1 else cost.swap_short_per_day) * days
        r = (d * (exit_px - entry) - swap) / risk
        rows.append((pd.Timestamp(sig_ts), pd.Timestamp(entry_ts), d, atr_val, entry, stop, target,
                     pd.Timestamp(exit_ts), exit_px, outcome, r,
                     int((exit_ts - entry_ts) / np.timedelta64(1, "m"))))
        busy_until = exit_ts
        if r < 0:
            last_loss_exit = exit_ts
    return pd.DataFrame(rows, columns=TRADE_COLUMNS)
