"""System-health checks (spec Section 12) — all read from the database, so they
catch an engine that crashed, hung, or was never started.

  feed:<SYMBOL>        newest stored M15 bar too old (24/7 symbols always; forex only in-session)
  core:<SYMBOL>|<MODE> Core hasn't refreshed the pair's state within 2 bars + grace
                       (not reported when Feed is already stale for that symbol — same root cause)
  validate             Tier 3 signals past their time limit + grace still have no outcome
  database             the database can't be reached at all (raised by the notifier)
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from config import MAX_HOLD_MINUTES, MODE_BAR_MINUTES, NotifyConfig
from storage import NotifyReader

ALWAYS_OPEN = {"SYNTHETIC", "CRYPTO"}


def forex_open(t: pd.Timestamp) -> bool:
    """Rough FX week: Sunday ~22:00 UTC to Friday ~21:00 UTC."""
    dow, hour = t.dayofweek, t.hour
    if dow == 5:
        return False
    if dow == 4:
        return hour < 21
    if dow == 6:
        return hour >= 22
    return True


def _ago(td: pd.Timedelta) -> str:
    m = int(td.total_seconds() // 60)
    return f"{m} min" if m < 120 else f"{m / 60:.1f} h" if m < 2880 else f"{m / 1440:.1f} days"


def check(reader: NotifyReader, cfg: NotifyConfig, now: datetime) -> dict[str, str]:
    now = pd.Timestamp(now)
    problems: dict[str, str] = {}
    feed = reader.feed_freshness()
    asset = dict(zip(feed["symbol"], feed["asset_class"])) if len(feed) else {}
    stale_feed = set()
    for r in feed.itertuples(index=False):
        lag = now - (pd.Timestamp(r.last_bar_ts) + pd.Timedelta(minutes=15))
        watch = r.asset_class in ALWAYS_OPEN or (
            forex_open(now) and forex_open(now - pd.Timedelta(minutes=cfg.feed_max_lag_minutes)))
        if watch and lag > pd.Timedelta(minutes=cfg.feed_max_lag_minutes):
            stale_feed.add(r.symbol)
            problems[f"feed:{r.symbol}"] = (
                f"Feed hasn't stored a {r.symbol} price bar for {_ago(lag)} "
                f"(newest {pd.Timestamp(r.last_bar_ts):%d %b %H:%M} UTC). "
                "Is `python main.py run` running in quantora_feed, and is MT5 open and logged in?")

    for r in reader.core_states().itertuples(index=False):
        bar = MODE_BAR_MINUTES.get(r.trade_mode, 240)
        limit = pd.Timedelta(minutes=2 * bar + cfg.core_grace_minutes)
        lag = now - pd.Timestamp(r.updated_at)
        cls = asset.get(r.symbol, "SYNTHETIC")
        in_session = cls in ALWAYS_OPEN or (forex_open(now) and forex_open(now - limit))
        if in_session and lag > limit and r.symbol not in stale_feed:
            problems[f"core:{r.symbol}|{r.trade_mode}"] = (
                f"Core hasn't evaluated {r.symbol} {r.trade_mode} for {_ago(lag)} (expected every "
                f"{_ago(pd.Timedelta(minutes=bar))}). Is `python main.py run` running in quantora_core?")

    late = []
    for r in reader.unlabelled_confirmed().itertuples(index=False):
        if r.close_ts is None or (not isinstance(r.close_ts, str) and pd.isna(r.close_ts)):
            continue
        due = pd.Timestamp(r.close_ts) + timedelta(minutes=MAX_HOLD_MINUTES.get(r.trade_mode, 20160))
        if now > due + timedelta(hours=cfg.validate_grace_hours):
            late.append(r.signal_id)
    if late:
        problems["validate"] = (
            f"{len(late)} Tier 3 signal(s) passed their time limit over {cfg.validate_grace_hours} h ago but still "
            "have no outcome. Is `python main.py run` running in quantora_validate? (If it is, a gap in stored "
            "prices may be blocking it — run Feed's backfill.)")
    return problems
