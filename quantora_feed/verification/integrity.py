"""Checks that need only the stored bars — no broker, no network.

These catch corrupt or impossible data (FAIL) and suspicious-but-maybe-fine
patterns (WARN): gaps, frozen prices, spikes, a feed that has stopped.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from config import SymbolSpec
from models import naive_utc
from verification.base import CheckResult, Status

BAR = pd.Timedelta(minutes=15)
ALWAYS_OPEN = {"SYNTHETIC", "CRYPTO"}  # Deriv synthetics and crypto CFDs trade 24/7
SPIKE_MULTIPLE = 15        # bar range vs median range
FROZEN_RUN_BARS = 8        # 2 hours of high == low
FRESH_LIMIT = pd.Timedelta(minutes=45)


def _expected_forex_gap(start: pd.Timestamp, end: pd.Timestamp) -> bool:
    """Weekend close, or a short daily break/rollover (gold pauses ~1h a day)."""
    if end - start <= pd.Timedelta(hours=2):
        return True
    weekend = start.dayofweek in (4, 5) and end.dayofweek in (6, 0) and end - start <= pd.Timedelta(hours=72)
    return weekend


def forex_session_open(now: pd.Timestamp) -> bool:
    """Rough FX week: Sunday ~22:00 UTC to Friday ~21:00 UTC."""
    dow, hour = now.dayofweek, now.hour
    if dow == 5:
        return False
    if dow == 4:
        return hour < 20
    if dow == 6:
        return hour >= 23
    return True


def _ago(delta: pd.Timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    return f"{minutes} min" if minutes < 120 else f"{minutes / 60:.1f} h"


def check_integrity(df: pd.DataFrame, spec: SymbolSpec, now_utc: datetime) -> list[CheckResult]:
    sym = spec.key
    now = naive_utc(now_utc)
    if df.empty:
        return [CheckResult(sym, "has data", Status.FAIL, "no stored bars in the window — run backfill")]

    df = df.sort_values("bar_ts").reset_index(drop=True)
    ts = pd.to_datetime(df["bar_ts"])
    o, h, l, c = (df[col].astype(float) for col in ("open_price", "high_price", "low_price", "close_price"))
    out = [CheckResult(sym, "has data", Status.PASS,
                       f"{len(df)} bars, {ts.iloc[0]:%Y-%m-%d %H:%M} → {ts.iloc[-1]:%Y-%m-%d %H:%M} UTC")]

    # -- impossible values: always FAIL ---------------------------------------
    bad_ohlc = (h < pd.concat([o, c], axis=1).max(axis=1)) | (l > pd.concat([o, c], axis=1).min(axis=1)) | (h < l)
    nonpos = (pd.concat([o, h, l, c], axis=1) <= 0).any(axis=1)
    problems = []
    if bad_ohlc.any():
        problems.append(f"{int(bad_ohlc.sum())} bars where high/low don't contain open/close "
                        f"(first {ts[bad_ohlc].iloc[0]})")
    if nonpos.any():
        problems.append(f"{int(nonpos.sum())} bars with zero/negative prices")
    misaligned = (ts.dt.minute % 15 != 0) | (ts.dt.second != 0)
    if misaligned.any():
        problems.append(f"{int(misaligned.sum())} timestamps not on a 15-minute boundary "
                        f"(e.g. {ts[misaligned].iloc[0]}) — broker offset is not a whole quarter-hour?")
    future = ts + BAR > now
    if future.any():
        problems.append(f"{int(future.sum())} bars that hadn't closed yet at {now:%H:%M} UTC "
                        f"(latest {ts.iloc[-1]}) — timestamps shifted into the future?")
    if ts.duplicated().any():
        problems.append(f"{int(ts.duplicated().sum())} duplicate timestamps")
    out.append(CheckResult(sym, "valid prices & times", Status.FAIL if problems else Status.PASS,
                           "; ".join(problems) or "OHLC consistent, prices > 0, 15-min aligned, no future bars"))

    # -- gaps -------------------------------------------------------------------
    diffs = ts.diff()
    gap_idx = diffs[diffs > BAR].index
    gaps = [(ts[i - 1], ts[i]) for i in gap_idx]
    if spec.asset_class not in ALWAYS_OPEN:
        gaps = [(a, b) for a, b in gaps if not _expected_forex_gap(a, b)]
    if gaps:
        missing = sum(int((b - a) / BAR) - 1 for a, b in gaps)
        worst = max(gaps, key=lambda g: g[1] - g[0])
        out.append(CheckResult(sym, "gaps", Status.WARN,
                               f"{len(gaps)} unexpected gap(s), {missing} missing bars; largest "
                               f"{worst[0]:%Y-%m-%d %H:%M} → {worst[1]:%Y-%m-%d %H:%M} "
                               "(broker maintenance/holiday, or Feed was down)"))
    else:
        note = "continuous 24/7" if spec.asset_class in ALWAYS_OPEN else "only weekend/daily-break gaps"
        out.append(CheckResult(sym, "gaps", Status.PASS, note))

    # -- frozen prices & spikes ------------------------------------------------
    flat = (h == l)
    run = flat.groupby((~flat).cumsum()).cumsum().max()
    rng = h - l
    med = rng[rng > 0].median()
    spikes = rng > SPIKE_MULTIPLE * med if pd.notna(med) and med > 0 else pd.Series(False, index=rng.index)
    notes, status = [], Status.PASS
    if run >= FROZEN_RUN_BARS:
        status = Status.WARN
        notes.append(f"price frozen for {int(run)} consecutive bars")
    if spikes.any():
        status = Status.WARN
        notes.append(f"{int(spikes.sum())} bar(s) with range > {SPIKE_MULTIPLE}× median "
                     f"(first {ts[spikes].iloc[0]}) — real news spike or bad tick?")
    out.append(CheckResult(sym, "frozen/spikes", status, "; ".join(notes) or "none found"))

    # -- freshness ---------------------------------------------------------------
    age = now - (ts.iloc[-1] + BAR)
    if spec.asset_class not in ALWAYS_OPEN and not forex_session_open(now):
        out.append(CheckResult(sym, "fresh", Status.SKIP, "market closed now"))
    elif age > FRESH_LIMIT:
        out.append(CheckResult(sym, "fresh", Status.WARN,
                               f"newest bar closed {_ago(age)} ago — is `python main.py run` running?"))
    else:
        out.append(CheckResult(sym, "fresh", Status.PASS, f"newest bar closed {_ago(age)} ago"))
    return out
