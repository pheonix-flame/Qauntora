"""Quantora Core engine: evaluate symbol/mode pairs and keep the state machine current.

Live (`run`) and history (`replay`) call the SAME evaluate() — live just looks
at the newest closed bar(s), replay at all of them. That's what lets the
backtester judge exactly the logic that runs live.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd

from config import CoreConfig, ModeProfile, SignalConfig
from confluence import SIDE, describe, evaluate, snapshot, suggestion
from news_filter import apply_blackout
from state_machine import is_upgrade_entry, state_key, transitions
from storage import CoreStore
from timeframes import rollup

log = logging.getLogger("quantora.core")

ALWAYS_OPEN = {"SYNTHETIC", "CRYPTO"}
MAX_CATCH_UP_BARS = 50


@dataclass
class PairResult:
    symbol: str
    mode: str
    status: str                 # evaluated | already evaluated | waiting for Feed | no data
    tier: str | None = None
    direction: str | None = None
    score: float | None = None
    notes: str = ""
    new_signals: int = 0
    bar_close_ts: pd.Timestamp | None = None
    config_source: str = ""
    warning: str = ""


def signal_config(store: CoreStore, cfg: CoreConfig, symbol: str, mode: str) -> SignalConfig:
    return store.approved_config(symbol, mode, cfg.defaults) or cfg.defaults


def closed_bars(m15: pd.DataFrame, mode: ModeProfile, now: datetime) -> pd.DataFrame:
    bars = rollup(m15, mode.timeframe)
    return bars[bars["close_ts"] <= pd.Timestamp(now)].reset_index(drop=True)


def evaluate_bars(store: CoreStore, cfg: CoreConfig, symbol: str, mode: ModeProfile, sigcfg: SignalConfig,
                  bars: pd.DataFrame) -> pd.DataFrame:
    evals = evaluate(bars, sigcfg, mode, cfg.asset(symbol))
    active = evals.attrs["active_conditions"]
    currencies = cfg.news_currencies.get(symbol, []) if cfg.asset(symbol) != "SYNTHETIC" else []
    if currencies and len(evals):
        start = pd.Timestamp(evals["close_ts"].iloc[0]) - timedelta(days=1)
        end = pd.Timestamp(evals["close_ts"].iloc[-1]) + timedelta(days=1)
        evals = apply_blackout(evals, store.news_events(start, end), currencies,
                               cfg.blackout_before_minutes, cfg.blackout_after_minutes)
    evals.attrs["active_conditions"] = active
    return evals


def evaluate_pair(store: CoreStore, cfg: CoreConfig, symbol: str, mode_name: str, now: datetime) -> PairResult:
    mode = cfg.modes[mode_name]
    sigcfg = signal_config(store, cfg, symbol, mode_name)
    res = PairResult(symbol, mode_name, "no data", config_source=sigcfg.source)
    since = pd.Timestamp(now) - timedelta(minutes=mode.bar_minutes * (mode.lookback_bars + 2))
    m15 = store.load_m15(symbol, since)
    if m15.empty:
        res.notes = f"no M15 bars for {symbol} — is Feed running and backfilled?"
        return res
    latest = m15["bar_ts"].max()
    lag = pd.Timestamp(now) - (latest + pd.Timedelta(minutes=15))
    if lag > pd.Timedelta(minutes=cfg.max_feed_lag_minutes) and cfg.asset(symbol) in ALWAYS_OPEN:
        res.warning = f"newest M15 bar is {int(lag.total_seconds() // 60)} min old — is Feed running?"
    bars = closed_bars(m15, mode, now)
    if bars.empty:
        res.notes = "not enough data for one closed bar"
        return res
    last_close = bars["close_ts"].iloc[-1]
    complete = latest >= last_close - pd.Timedelta(minutes=15)
    if not complete and pd.Timestamp(now) - last_close < pd.Timedelta(minutes=cfg.max_feed_lag_minutes):
        res.status, res.bar_close_ts = "waiting for Feed", last_close
        res.notes = f"bar closing {last_close} isn't fully stored yet"
        return res

    evals = evaluate_bars(store, cfg, symbol, mode, sigcfg, bars)
    active = evals.attrs.get("active_conditions", [])
    prev = store.get_state(symbol, mode_name)
    prev_close = pd.Timestamp(prev["snapshot"]["close_ts"]) if prev and prev.get("snapshot", {}).get("close_ts") \
        else None
    prev_version = prev["snapshot"].get("config", {}).get("version_id") if prev else None
    row = evals.iloc[-1]
    res.bar_close_ts = row["close_ts"]
    if prev_close is not None and prev_close >= row["close_ts"] and prev_version == sigcfg.version_id:
        res.status, res.tier, res.direction = "already evaluated", prev["tier"], prev["direction"]
        res.score, res.notes = prev["score"], prev["notes"]
        return res

    # Bars since the last evaluation (catch-up if Core was offline), oldest first.
    new = evals[evals["close_ts"] > prev_close] if prev_close is not None else evals.tail(1)
    if len(new) > MAX_CATCH_UP_BARS:
        log.warning("%s %s: %d bars since last run — logging only the latest", symbol, mode_name, len(new))
        new = new.tail(1)
    start_key = state_key(prev["tier"], prev["direction"]) if prev else ("WATCHING", None)
    for _, t in transitions(new, start_key).iterrows():
        snap = snapshot(t, sigcfg, active) | {"prev_tier": t["prev_tier"]}
        sug = suggestion(t, mode) if t["tier"] == "CONFIRMED" else {"entry": None, "stop": None, "target": None}
        sid = store.append_signal(symbol, mode_name, t["tier"], SIDE.get(int(t["direction"])), float(t["score"]),
                                  sug["entry"], sug["stop"], sug["target"], snap)
        res.new_signals += 1
        log.info("%s %s: %s -> %s %s (signal %s, bar %s)", symbol, mode_name, t["prev_tier"], t["tier"],
                 SIDE.get(int(t["direction"]), ""), sid, t["close_ts"])

    side = SIDE.get(int(row["direction"]))
    notes = describe(row, symbol, mode, sigcfg, active)
    store.save_state(symbol, mode_name, row["tier"], side, float(row["score"]),
                     snapshot(row, sigcfg, active), notes)
    store.commit()
    res.status, res.tier, res.direction, res.score, res.notes = "evaluated", row["tier"], side, \
        float(row["score"]), notes
    return res


def replay(store: CoreStore, cfg: CoreConfig, symbol: str, mode_name: str,
           since: datetime | None = None, now: datetime | None = None) -> tuple[pd.DataFrame, SignalConfig]:
    """Every tier change over history — what `run` would have logged. Writes nothing to the DB."""
    mode = cfg.modes[mode_name]
    sigcfg = signal_config(store, cfg, symbol, mode_name)
    m15 = store.load_m15(symbol, since)
    if m15.empty:
        return pd.DataFrame(), sigcfg
    bars = closed_bars(m15, mode, now or (m15["bar_ts"].max() + pd.Timedelta(minutes=15)))
    evals = evaluate_bars(store, cfg, symbol, mode, sigcfg, bars)
    active = evals.attrs.get("active_conditions", [])
    tr = transitions(evals)
    if tr.empty:
        return pd.DataFrame(), sigcfg
    rows = []
    for _, t in tr.iterrows():
        side = SIDE.get(int(t["direction"])) if t["tier"] != "WATCHING" else None
        sug = suggestion(t, mode) if t["tier"] == "CONFIRMED" else {"entry": None, "stop": None, "target": None}
        rows.append({
            "signal_ts": t["close_ts"], "bar_ts": t["bar_ts"], "symbol": symbol, "mode": mode_name,
            "tier": t["tier"], "prev_tier": t["prev_tier"], "direction": side, "prev_direction": t["prev_direction"],
            "is_entry": bool(side) and is_upgrade_entry(t["tier"], t["prev_tier"], t["prev_direction"], side),
            "score": round(float(t["score"]), 4), "entry": sug["entry"], "stop": sug["stop"], "target": sug["target"],
            "notes": describe(t, symbol, mode, sigcfg, active)})
    return pd.DataFrame(rows), sigcfg
