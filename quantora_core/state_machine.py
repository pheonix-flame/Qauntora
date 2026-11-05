"""The per-symbol/mode three-tier state machine (spec Section 4).

State key = (tier, direction), with direction ignored while WATCHING so a
drifting EMA doesn't spam the log. A change of key is a TRANSITION: it is
appended to qtx_signals (the audit log). A CONFIRMED state that persists over
several bars is ONE signal — it doesn't re-fire every bar. After it drops back
to FORMING/WATCHING, a fresh confirmation is a new signal.
"""
from __future__ import annotations

import pandas as pd

from confluence import SIDE

RANK = {"WATCHING": 0, "FORMING": 1, "CONFIRMED": 2}


def state_key(tier: str, direction) -> tuple:
    """direction may be +1/-1 (fresh evaluation) or 'BUY'/'SELL' (stored state)."""
    if tier == "WATCHING" or direction is None or (not isinstance(direction, str) and pd.isna(direction)):
        return (tier, None)
    if isinstance(direction, str):
        return (tier, direction if direction in ("BUY", "SELL") else None)
    return (tier, SIDE.get(int(direction)))


def transitions(evals: pd.DataFrame, start_key: tuple = ("WATCHING", None)) -> pd.DataFrame:
    """Rows of `evals` where the state key changes, with prev_tier/prev_direction added."""
    keys = [state_key(t, d) for t, d in zip(evals["tier"], evals["direction"])]
    rows, prev = [], start_key
    for i, k in enumerate(keys):
        if k != prev:
            rows.append((evals.index[i], prev[0], prev[1]))
            prev = k
    if not rows:
        return evals.iloc[0:0].assign(prev_tier=pd.Series(dtype=str), prev_direction=pd.Series(dtype=str))
    idx, ptier, pdir = zip(*rows)
    out = evals.loc[list(idx)].copy()
    out["prev_tier"], out["prev_direction"] = list(ptier), list(pdir)
    return out


def is_upgrade_entry(tier: str, prev_tier: str, prev_direction, direction: str) -> bool:
    """Would this transition be an *entry* at that tier? (used to compare Tier 2 vs Tier 3 entries)"""
    return RANK[tier] > RANK.get(prev_tier, 0) or (tier != "WATCHING" and direction != prev_direction)
