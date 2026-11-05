"""News blackout (spec Section 10, Core): no NEW Tier 3 around high-impact events.

A CONFIRMED evaluation inside [event - before, event + after] for one of the
symbol's currencies is downgraded to FORMING, with the event named in the notes.
Synthetic indices have no currencies and are never blacked out.

In replay, only events Feed has actually collected can black anything out —
the free calendar is weekly, so older history has no news coverage.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def apply_blackout(evals: pd.DataFrame, events: pd.DataFrame, currencies: list[str],
                   before_min: int, after_min: int) -> pd.DataFrame:
    if not currencies or events is None or events.empty or evals.empty:
        return evals
    ev = events[(events["impact"] == "HIGH") & events["currency"].isin(currencies)].sort_values("event_ts")
    if ev.empty:
        return evals
    times = pd.to_datetime(ev["event_ts"]).to_numpy()
    titles = (ev["currency"] + " " + ev["title"]).to_numpy()
    decision = pd.to_datetime(evals["close_ts"]).to_numpy()
    # nearest event at or after (decision - after): it's in the window if it's also <= decision + before
    idx = np.searchsorted(times, decision - np.timedelta64(after_min, "m"), side="left")
    has = idx < len(times)
    hit = np.zeros(len(decision), dtype=bool)
    hit[has] = times[idx[has]] <= decision[has] + np.timedelta64(before_min, "m")
    out = evals.copy()
    labels = np.where(hit, [f"{titles[i]} at {pd.Timestamp(times[i]):%H:%M} UTC" if h else None
                            for i, h in zip(np.clip(idx, 0, len(times) - 1), hit)], None)
    out["blackout"] = labels
    out.loc[hit & (out["tier"] == "CONFIRMED").to_numpy(), "tier"] = "FORMING"
    return out
