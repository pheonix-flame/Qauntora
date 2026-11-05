"""Triple Barrier labelling (spec Section 6) on the stored M15 price path.

  upper/lower barrier   the signal's take-profit and stop-loss
  vertical barrier      mode-specific max holding time (45 min / 2 weeks / 3 months)

The path starts at the first M15 bar at/after the decision time (the signal
bar's close) — the earliest anyone could have acted. Same pessimistic rules
as the backtester, so live labels and backtest results are comparable:
  * SL and TP inside the same M15 bar -> LOSS (can't know the order; assume the worse)
  * a bar that opens beyond the stop (gap) resolves at that worse open price
  * TP resolves exactly at TP
  * TIMEOUT resolves at the first available open at/after the deadline
    (after a weekend that's the reopen — gap included)
Prices are the stored BID bars; spread/slippage are NOT applied here (this
labels whether the *signal* was right; trading costs are the backtester's
and, later, Sentinel's job).

R-multiple = direction x (resolved - entry) / |entry - stop|.

A label is only produced when it's certain. Otherwise PENDING with a reason:
  * the trade hasn't resolved yet and the deadline hasn't passed in the data
  * a 24/7 symbol has a hole in its stored prices along the path — a barrier
    could have been touched inside the missing bars, so we don't guess
    (re-run Feed's backfill to fill it).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

BAR = np.timedelta64(15, "m")


@dataclass(frozen=True)
class Label:
    outcome: str | None        # WIN | LOSS | TIMEOUT, or None = still pending
    r_multiple: float | None = None
    resolved_price: float | None = None
    resolved_at: pd.Timestamp | None = None
    reason: str = ""


def label(m15: pd.DataFrame, decision_ts, direction: int, entry: float, stop: float, target: float,
          max_hold_minutes: int, always_open: bool) -> Label:
    """m15: bar_ts, open/high/low/close_price — oldest first, naive UTC."""
    if direction not in (1, -1):
        return Label(None, reason="no direction")
    risk = abs(entry - stop)
    if not np.isfinite(risk) or risk <= 0 or direction * (target - entry) <= 0 or direction * (entry - stop) <= 0:
        return Label(None, reason="invalid barriers (stop/target on the wrong side of entry)")
    ts = m15["bar_ts"].to_numpy()
    o = m15["open_price"].to_numpy(float)
    h = m15["high_price"].to_numpy(float)
    lo = m15["low_price"].to_numpy(float)
    start = np.datetime64(pd.Timestamp(decision_ts))
    deadline = start + np.timedelta64(int(max_hold_minutes), "m")
    i = int(np.searchsorted(ts, start, side="left"))
    if i >= len(ts):
        return Label(None, reason="no price data after the signal yet")
    j_end = int(np.searchsorted(ts, deadline, side="left"))  # bars starting before the deadline
    path = slice(i, j_end)
    if direction == 1:
        hit_sl, hit_tp = lo[path] <= stop, h[path] >= target
    else:
        hit_sl, hit_tp = h[path] >= stop, lo[path] <= target
    k_sl = int(np.argmax(hit_sl)) if hit_sl.any() else None
    k_tp = int(np.argmax(hit_tp)) if hit_tp.any() else None
    first = min(k for k in (k_sl, k_tp, j_end - i) if k is not None)

    if always_open:  # a hole before the resolution point means we can't be sure
        seg = ts[i:i + first + 1]
        late_start = ts[i] - start >= BAR  # first bar after the decision is missing
        if late_start or (len(seg) > 1 and (np.diff(seg) > BAR).any()):
            return Label(None, reason="gap in stored prices along the path — run Feed backfill")

    if k_sl is not None and (k_tp is None or k_sl <= k_tp):
        k = i + k_sl
        px = min(stop, o[k]) if direction == 1 else max(stop, o[k])
        return _done("LOSS", px, ts[k] + BAR, entry, risk, direction)
    if k_tp is not None:
        k = i + k_tp
        return _done("WIN", target, ts[k] + BAR, entry, risk, direction)
    if j_end >= len(ts):
        return Label(None, reason="still open — neither barrier hit and the deadline hasn't passed in the data")
    return _done("TIMEOUT", o[j_end], ts[j_end], entry, risk, direction)


def _done(outcome, px, at, entry, risk, d) -> Label:
    return Label(outcome, round(float(d * (px - entry) / risk), 3), float(px), pd.Timestamp(at))


def tier2_barriers(snapshot: dict, direction: int, sl_atr: float, tp_atr: float) -> tuple[float, float, float] | None:
    """Hypothetical entry/stop/target for a FORMING entry, from the bar it formed on."""
    close, atr = snapshot.get("close"), snapshot.get("atr")
    if close is None or atr is None or atr <= 0:
        return None
    return close, close - direction * sl_atr * atr, close + direction * tp_atr * atr
