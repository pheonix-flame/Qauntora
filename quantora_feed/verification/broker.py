"""Cross-checks against the broker itself (needs MT5 running).

round_trip   stored M15 bars vs a fresh pull from MT5 — proves nothing was lost
             or altered on the way into the database (rounding, truncation, gaps).
h1_rebuild   stored M15 bars rolled up into hours vs MT5's *own* H1 bars — an
             independent product of the broker. If our M15 set is complete and
             correctly bucketed, open/high/low/close match exactly.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from verification.base import CheckResult, Status

PRICE_COLS = ["open_price", "high_price", "low_price", "close_price"]
ATOL = 2e-6  # Oracle NUMBER(18,6) rounds to 6 dp


def _prices_equal(a: pd.DataFrame, b: pd.DataFrame) -> pd.Series:
    eq = pd.Series(True, index=a.index)
    for col in PRICE_COLS:
        eq &= np.isclose(a[col].astype(float), b[col].astype(float), atol=ATOL, rtol=0)
    return eq


def round_trip(symbol: str, stored: pd.DataFrame, fresh: pd.DataFrame) -> CheckResult:
    if stored.empty or fresh.empty:
        return CheckResult(symbol, "matches MT5 (M15)", Status.SKIP, "nothing to compare")
    last_stored = pd.Timestamp(stored["bar_ts"].max())
    fresh = fresh[fresh["bar_ts"] <= last_stored]  # ignore bars Feed simply hasn't pulled yet
    m = stored.merge(fresh, on="bar_ts", how="outer", suffixes=("", "_mt5"), indicator=True)
    only_db = int((m["_merge"] == "left_only").sum())
    only_mt5 = int((m["_merge"] == "right_only").sum())
    both = m[m["_merge"] == "both"]
    right = both[[f"{c}_mt5" for c in PRICE_COLS]].set_axis(PRICE_COLS, axis=1)
    differ = both[~_prices_equal(both[PRICE_COLS], right)]
    if len(differ) or only_db:
        first = differ.iloc[0] if len(differ) else None
        detail = []
        if len(differ):
            detail.append(f"{len(differ)} bars with different prices, e.g. {first['bar_ts']}: "
                          f"DB close {first['close_price']} vs MT5 {first['close_price_mt5']}")
        if only_db:
            detail.append(f"{only_db} stored bars MT5 doesn't have at those times — timestamp shift?")
        return CheckResult(symbol, "matches MT5 (M15)", Status.FAIL, "; ".join(detail))
    if only_mt5:
        return CheckResult(symbol, "matches MT5 (M15)", Status.WARN,
                           f"{len(both)} bars identical, but {only_mt5} bars MT5 has are missing from the DB "
                           "— re-run backfill for this window")
    return CheckResult(symbol, "matches MT5 (M15)", Status.PASS, f"all {len(both)} bars identical")


def rebuild_h1(m15: pd.DataFrame, h1_times: pd.Series) -> pd.DataFrame:
    """Bucket M15 bars into the broker's own H1 windows (offset-agnostic)."""
    h1_ts = np.sort(pd.to_datetime(h1_times).to_numpy())
    m = m15.sort_values("bar_ts")
    ts = pd.to_datetime(m["bar_ts"]).to_numpy()
    idx = np.searchsorted(h1_ts, ts, side="right") - 1
    valid = (idx >= 0) & (ts < h1_ts[np.clip(idx, 0, None)] + np.timedelta64(60, "m"))
    m = m[valid].assign(h1=h1_ts[idx[valid]])
    return m.groupby("h1").agg(open_price=("open_price", "first"), high_price=("high_price", "max"),
                               low_price=("low_price", "min"), close_price=("close_price", "last"),
                               volume=("volume", "sum"), bars=("bar_ts", "size")).reset_index()


def h1_rebuild(symbol: str, stored_m15: pd.DataFrame, broker_h1: pd.DataFrame) -> CheckResult:
    name = "rebuilds MT5 H1"
    if stored_m15.empty or broker_h1.empty:
        return CheckResult(symbol, name, Status.SKIP, "nothing to compare")
    lo, hi = pd.Timestamp(stored_m15["bar_ts"].min()), pd.Timestamp(stored_m15["bar_ts"].max())
    # only hours fully inside the stored window
    h1 = broker_h1[(broker_h1["bar_ts"] >= lo) & (broker_h1["bar_ts"] + pd.Timedelta(minutes=60)
                                                  <= hi + pd.Timedelta(minutes=15))]
    if h1.empty:
        return CheckResult(symbol, name, Status.SKIP, "window shorter than one full hour")
    rebuilt = rebuild_h1(stored_m15, h1["bar_ts"])
    m = h1.merge(rebuilt, left_on="bar_ts", right_on="h1", how="left", suffixes=("_mt5", ""))
    missing = m["h1"].isna()
    ok = m[~missing]
    left = ok[[f"{c}_mt5" for c in PRICE_COLS]].set_axis(PRICE_COLS, axis=1)
    differ = ok[~_prices_equal(ok[PRICE_COLS], left)]
    if missing.any() or len(differ):
        parts = []
        if missing.any():
            parts.append(f"{int(missing.sum())} broker hours with no stored M15 bars")
        if len(differ):
            r = differ.iloc[0]
            parts.append(f"{len(differ)} hours don't match, e.g. {r['bar_ts']}: rebuilt "
                         f"O/H/L/C {r['open_price']}/{r['high_price']}/{r['low_price']}/{r['close_price']} vs MT5 "
                         f"{r['open_price_mt5']}/{r['high_price_mt5']}/{r['low_price_mt5']}/{r['close_price_mt5']}")
        return CheckResult(symbol, name, Status.FAIL, "; ".join(parts) + " — M15 bars missing or altered")
    vol_off = int((~np.isclose(ok["volume"].astype(float), ok["volume_mt5"].astype(float))).sum())
    if vol_off:
        return CheckResult(symbol, name, Status.WARN,
                           f"prices match for all {len(ok)} hours; tick volume differs in {vol_off}")
    return CheckResult(symbol, name, Status.PASS, f"all {len(ok)} hours match exactly (prices and volume)")
