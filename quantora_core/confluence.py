"""Confluence scoring and the three tiers (spec Sections 4, 5, 9).

For every CLOSED bar of the mode's timeframe:

  direction   +1 (BUY) if EMA fast > EMA slow, -1 (SELL) if below, 0 otherwise.
  conditions  checked FOR THAT DIRECTION, one per market dimension:
                trend     close on the trend side of the slow EMA
                strength  ADX >= adx_min                    (filter, not direction)
                momentum  RSI in (50, extreme) for BUY / (100-extreme, 50) for SELL,
                          or MACD histogram sign — one or the other, not both
                location  inside the Bollinger Bands — not stretched (no-FOMO)
                volume    OBV moving with the trend (CRYPTO only)
  score       weighted share of conditions met, 0..1
  trigger     the "key confirmation": bar closes in the trade direction AND beyond
              the previous close (a confirmed bullish/bearish close)
  chase       trigger bar's range > chase_atr x ATR -> entering now would be chasing

  CONFIRMED (Tier 3)  score >= tier3 AND trigger AND not chase   (news checked later)
  FORMING   (Tier 2)  score >= tier2 (but not confirmed)
  WATCHING  (Tier 1)  everything else, including indicator warm-up

Every value uses only bars up to and including the one being evaluated.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import indicators as ind
from config import ModeProfile, SignalConfig

TIERS = ("WATCHING", "FORMING", "CONFIRMED")
CONDITIONS = ("trend", "strength", "momentum", "location", "volume")
SIDE = {1: "BUY", -1: "SELL"}


def evaluate(bars: pd.DataFrame, cfg: SignalConfig, mode: ModeProfile, asset_class: str) -> pd.DataFrame:
    """One row per bar: indicators, conditions, score, trigger, tier, missing items."""
    p, w = cfg.params, cfg.weights
    close, open_ = bars["close_price"], bars["open_price"]
    out = pd.DataFrame({"bar_ts": bars["bar_ts"].to_numpy(), "close_ts": bars["close_ts"].to_numpy(),
                        "open": open_.to_numpy(), "high": bars["high_price"].to_numpy(),
                        "low": bars["low_price"].to_numpy(), "close": close.to_numpy()}, index=bars.index)

    out["ema_fast"] = ind.ema(close, p["ema_fast"])
    out["ema_slow"] = ind.ema(close, p["ema_slow"])
    a = ind.adx(bars, p["adx_period"])
    out["adx"], out["plus_di"], out["minus_di"] = a["adx"], a["plus_di"], a["minus_di"]
    out["atr"] = ind.atr(bars, mode.atr_period)
    bb = ind.bollinger(close, p["bb_period"], p["bb_std"])
    out["pct_b"] = bb["pct_b"]
    use_macd = p["momentum"].upper() == "MACD"
    if use_macd:
        out["macd_hist"] = ind.macd(close, p["macd_fast"], p["macd_slow"], p["macd_signal"])["hist"]
        mom = out["macd_hist"]
    else:
        out["rsi"] = ind.rsi(close, p["rsi_period"])
        mom = out["rsi"]
    crypto = asset_class == "CRYPTO"
    if crypto:
        o = ind.obv(bars)
        out["obv_slope"] = o - o.shift(p["obv_slope_bars"])

    d = np.sign(out["ema_fast"] - out["ema_slow"]).fillna(0).astype(int)
    out["direction"] = d
    buy, sell = d == 1, d == -1
    ext = p["rsi_extreme"]
    cond = {
        "trend": (buy & (close > out["ema_slow"])) | (sell & (close < out["ema_slow"])),
        "strength": out["adx"] >= p["adx_min"],
        "momentum": ((buy & (mom > 0)) | (sell & (mom < 0))) if use_macd else
                    ((buy & (mom > 50) & (mom < ext)) | (sell & (mom < 50) & (mom > 100 - ext))),
        "location": (buy & (out["pct_b"] < 1)) | (sell & (out["pct_b"] > 0)),
        "volume": ((buy & (out["obv_slope"] > 0)) | (sell & (out["obv_slope"] < 0))) if crypto else
                  pd.Series(False, index=bars.index),
    }
    active = [c for c in CONDITIONS if w.get(c, 0) > 0 and (c != "volume" or crypto)]
    total_w = sum(w[c] for c in active)
    out["score"] = sum(cond[c].astype(float) * w[c] for c in active) / total_w if total_w else 0.0
    for c in CONDITIONS:
        out[f"c_{c}"] = cond[c].astype(bool) if c in active else pd.NA

    prev_close = close.shift(1)
    out["trigger"] = (buy & (close > open_) & (close > prev_close)) | (sell & (close < open_) & (close < prev_close))
    out["chase"] = (bars["high_price"] - bars["low_price"]) > p["chase_atr"] * out["atr"]

    needed = ["ema_slow", "adx", "atr", "pct_b", "macd_hist" if use_macd else "rsi"] + (["obv_slope"] if crypto else [])
    out["warm"] = out[needed].notna().all(axis=1)

    live = out["warm"] & (d != 0)
    confirmed = live & (out["score"] >= cfg.tier3_threshold - 1e-9) & out["trigger"] & ~out["chase"]
    forming = live & (out["score"] >= cfg.tier2_threshold - 1e-9) & ~confirmed
    out["tier"] = np.select([confirmed, forming], ["CONFIRMED", "FORMING"], "WATCHING")
    out["blackout"] = None
    out.attrs["active_conditions"] = active
    return out


def missing_items(row: pd.Series, active: list[str], cfg: SignalConfig) -> list[str]:
    side = SIDE.get(int(row["direction"]))
    if not row["warm"]:
        return ["indicator warm-up (not enough history yet)"]
    if side is None:
        return ["a trend direction (EMAs are flat)"]
    p, up = cfg.params, side == "BUY"
    labels = {
        "trend": f"close {'above' if up else 'below'} EMA{p['ema_slow']}",
        "strength": f"ADX >= {p['adx_min']:g} (now {row['adx']:.1f})",
        "momentum": (f"MACD histogram {'> 0' if up else '< 0'}" if "macd_hist" in row else
                     f"RSI {'50–' + format(p['rsi_extreme'], 'g') if up else format(100 - p['rsi_extreme'], 'g') + '–50'}"
                     f" (now {row.get('rsi', float('nan')):.1f})"),
        "location": f"price back inside the Bollinger Bands (%b now {row['pct_b']:.2f})",
        "volume": "OBV moving with the trend",
    }
    items = [labels[c] for c in active if not bool(row[f"c_{c}"])]
    if not row["trigger"]:
        items.append(f"a confirmed {'bullish' if up else 'bearish'} close")
    elif row["chase"]:
        items.append(f"a calmer trigger bar (range > {p['chase_atr']:g}×ATR — not chasing)")
    if row.get("blackout"):
        items.append(f"news blackout to clear ({row['blackout']})")
    return items


def suggestion(row: pd.Series, mode: ModeProfile) -> dict:
    """Entry/SL/TP for a confirmed signal: entry ~ trigger close, barriers in ATRs (same as backtest)."""
    d, px, a = int(row["direction"]), float(row["close"]), float(row["atr"])
    return {"entry": px, "stop": px - d * mode.sl_atr * a, "target": px + d * mode.tp_atr * a}


def describe(row: pd.Series, symbol: str, mode: ModeProfile, cfg: SignalConfig, active: list[str]) -> str:
    """Human summary — goes into qtx_signal_state.notes and, later, Notify's Telegram message."""
    side = SIDE.get(int(row["direction"]), "—")
    head = f"{symbol} {mode.name}"
    tier, score = row["tier"], float(row["score"]) if pd.notna(row["score"]) else 0.0
    if tier == "CONFIRMED":
        s = suggestion(row, mode)
        return (f"{head}: CONFIRMED {side} near {s['entry']:.6g} (score {score:.2f}). "
                f"SL {s['stop']:.6g} / TP {s['target']:.6g} ({mode.sl_atr:g}/{mode.tp_atr:g} ATR). "
                "Size is set by Sentinel from live balance.")[:400]
    missing = missing_items(row, active, cfg)
    if tier == "FORMING":
        return (f"{head}: Setup forming {side} (score {score:.2f}). Watching for: "
                f"{'; '.join(missing)}. No trade yet.")[:400]
    return (f"{head}: Watching{' ' + side if side != '—' else ''} (score {score:.2f}). Missing: "
            f"{'; '.join(missing) or '—'}.")[:400]


def snapshot(row: pd.Series, cfg: SignalConfig, active: list[str]) -> dict:
    """Everything that produced this state — stored as JSON for audit and Calibrate."""
    def num(k):
        v = row.get(k)
        return None if v is None or pd.isna(v) else round(float(v), 6)
    snap = {
        "bar_ts": str(row["bar_ts"]), "close_ts": str(row["close_ts"]),
        "close": num("close"), "ema_fast": num("ema_fast"), "ema_slow": num("ema_slow"),
        "adx": num("adx"), "plus_di": num("plus_di"), "minus_di": num("minus_di"),
        "rsi": num("rsi"), "macd_hist": num("macd_hist"), "pct_b": num("pct_b"),
        "obv_slope": num("obv_slope"), "atr": num("atr"),
        "conditions": {c: bool(row[f"c_{c}"]) for c in active},
        "trigger": bool(row["trigger"]), "chase": bool(row["chase"]), "warm": bool(row["warm"]),
        "score": num("score"), "blackout": row.get("blackout"),
        "config": {"source": cfg.source, "version_id": cfg.version_id,
                   "tier2_threshold": cfg.tier2_threshold, "tier3_threshold": cfg.tier3_threshold},
    }
    return {k: v for k, v in snap.items() if v is not None}
