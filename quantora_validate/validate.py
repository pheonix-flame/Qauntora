"""Label every unlabelled Core signal whose outcome is now knowable."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

import pandas as pd

from config import ValidateConfig
from storage import ValidateStore
from triple_barrier import label, tier2_barriers

log = logging.getLogger("quantora.validate")
ALWAYS_OPEN = {"SYNTHETIC", "CRYPTO"}
SIDE = {"BUY": 1, "SELL": -1}


@dataclass
class RunSummary:
    labelled: int = 0
    by_outcome: dict = field(default_factory=dict)
    pending: dict = field(default_factory=dict)   # reason -> count
    skipped: dict = field(default_factory=dict)   # reason -> count (never labelled)

    def add(self, bucket: dict, key: str) -> None:
        bucket[key] = bucket.get(key, 0) + 1


def is_tier2_entry(sig) -> bool:
    """A FORMING row is an 'acted early' entry only when it was reached from WATCHING, or by a flip."""
    snap = sig["snapshot"] or {}
    prev_tier, prev_dir = snap.get("prev_tier"), snap.get("prev_direction")
    return prev_tier == "WATCHING" or (prev_dir is not None and prev_dir != sig["direction"])


def run_once(store: ValidateStore, cfg: ValidateConfig) -> RunSummary:
    summary = RunSummary()
    sigs = store.unlabelled_signals()
    if sigs.empty:
        return summary
    sigs["decision_ts"] = [pd.Timestamp(s.get("close_ts")) if s.get("close_ts") else pd.Timestamp(c)
                           for s, c in zip(sigs["snapshot"], sigs["created_at"])]
    rows = []
    for symbol, group in sigs.groupby("symbol"):
        holds = [cfg.modes[m].max_hold_minutes for m in group["trade_mode"] if m in cfg.modes]
        if not holds:
            summary.add(summary.skipped, "unknown mode")
            continue
        start = group["decision_ts"].min() - timedelta(hours=1)
        end = group["decision_ts"].max() + timedelta(minutes=max(holds)) + timedelta(days=4)
        m15, asset = store.load_m15(symbol, start.to_pydatetime(), end.to_pydatetime())
        for _, sig in group.iterrows():
            mode = cfg.modes.get(sig["trade_mode"])
            if mode is None:
                summary.add(summary.skipped, f"unknown mode {sig['trade_mode']}")
                continue
            d = SIDE[sig["direction"]]
            if sig["tier"] == "FORMING":
                if not cfg.tier2_hypothetical or not is_tier2_entry(sig):
                    summary.add(summary.skipped, "Tier 2 row that isn't an entry (e.g. decay from Tier 3)")
                    continue
                bars = tier2_barriers(sig["snapshot"], d, mode.sl_atr, mode.tp_atr)
            else:
                stored = (sig["entry_price"], sig["stop_loss"], sig["take_profit"])
                bars = tuple(float(x) for x in stored) if all(pd.notna(x) for x in stored) else \
                    tier2_barriers(sig["snapshot"], d, mode.sl_atr, mode.tp_atr)
            if bars is None:
                summary.add(summary.skipped, "no entry/stop/target and no snapshot to rebuild them")
                continue
            entry, stop, target = bars
            lab = label(m15, sig["decision_ts"], d, entry, stop, target, mode.max_hold_minutes,
                        always_open=asset in ALWAYS_OPEN)
            if lab.outcome is None:
                summary.add(summary.pending, lab.reason)
                continue
            rows.append({"signal_id": int(sig["signal_id"]), "outcome": lab.outcome, "r_multiple": lab.r_multiple,
                         "resolved_price": lab.resolved_price, "resolved_at": lab.resolved_at})
            summary.add(summary.by_outcome, f"{sig['tier']} {lab.outcome}")
    summary.labelled = store.insert_outcomes(rows)
    return summary


def report(df: pd.DataFrame) -> pd.DataFrame:
    """Per symbol/mode/tier: n, win rate, expectancy — the live ground truth Calibrate will use."""
    if df.empty:
        return df
    g = df.groupby(["symbol", "trade_mode", "tier"])
    out = g.agg(n=("r_multiple", "size"), win_rate=("outcome", lambda s: (s == "WIN").mean()),
                expectancy_r=("r_multiple", "mean"), timeouts=("outcome", lambda s: (s == "TIMEOUT").sum()))
    return out.reset_index().round({"win_rate": 3, "expectancy_r": 3})
