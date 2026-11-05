"""Message texts (Telegram HTML). Everything user-visible lives here."""
from __future__ import annotations

from html import escape as _html_escape

import pandas as pd

def escape(text) -> str:
    """Telegram HTML only needs <, > and & escaped; leave quotes readable."""
    return _html_escape(str(text), quote=False)


EMOJI = {"CONFIRMED": "🟢", "FORMING": "🟡", "CANCELLED": "⚪", "WIN": "✅", "LOSS": "❌", "TIMEOUT": "⏱",
         "HEALTH": "⚠️", "RECOVERED": "✅", "INFO": "ℹ️"}


def price(x) -> str:
    if x is None or pd.isna(x):
        return "—"
    x = float(x)
    dp = 2 if abs(x) >= 1000 else 4 if abs(x) >= 10 else 5
    return f"{x:,.{dp}f}"


def _when(ts) -> str:
    return "" if ts is None or (not isinstance(ts, str) and pd.isna(ts)) else f"{pd.Timestamp(ts):%d %b %H:%M} UTC"


def watching_for(snap: dict, direction: str) -> list[str]:
    """What a FORMING setup still needs, from Core's snapshot."""
    up = direction == "BUY"
    labels = {
        "trend": f"close {'above' if up else 'below'} the slow EMA",
        "strength": f"a stronger trend (ADX now {snap['adx']:.1f})" if snap.get("adx") is not None else "a stronger trend",
        "momentum": _momentum_need(snap.get("rsi"), up),
        "location": "price back inside the Bollinger Bands (not stretched)",
        "volume": "volume (OBV) moving with the trend",
    }
    items = [labels[k] for k, ok in (snap.get("conditions") or {}).items() if not ok and k in labels]
    if not snap.get("trigger"):
        items.append(f"a confirmed {'bullish' if up else 'bearish'} close")
    elif snap.get("chase"):
        items.append("a calmer trigger candle (this one is too big — not chasing)")
    if snap.get("blackout"):
        items.append(f"the news blackout to pass ({snap['blackout']})")
    return items


def _momentum_need(rsi, up: bool) -> str:
    """The momentum condition failed: say which way. For a BUY, RSI above 50 here means overbought."""
    if rsi is None:
        return "momentum (MACD) to agree"
    if up:
        return (f"RSI to cool off from overbought (now {rsi:.1f})" if rsi > 50
                else f"momentum to turn up (RSI now {rsi:.1f})")
    return (f"RSI to recover from oversold (now {rsi:.1f})" if rsi < 50
            else f"momentum to turn down (RSI now {rsi:.1f})")


def tier3(sig) -> str:
    snap, d = sig["snapshot"] or {}, sig["direction"]
    entry, stop, target = sig["entry_price"], sig["stop_loss"], sig["take_profit"]
    rr = ""
    try:
        rr = f" (reward:risk {abs(float(target) - float(entry)) / abs(float(entry) - float(stop)):.1f}:1)"
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    return (f"{EMOJI['CONFIRMED']} <b>{escape(sig['symbol'])} {escape(sig['trade_mode'])} — CONFIRMED {d}</b>\n"
            f"Entry ≈ {price(entry)}\n"
            f"Stop {price(stop)} · Target {price(target)}{rr}\n"
            f"Score {float(sig['score']):.2f} · candle closed {_when(snap.get('close_ts'))}\n"
            f"<i>Size it at your risk % (Sentinel will do this automatically once built). Signal #{sig['signal_id']}.</i>")


def tier2(sig) -> str:
    snap, d = sig["snapshot"] or {}, sig["direction"]
    need = watching_for(snap, d)
    return (f"{EMOJI['FORMING']} <b>{escape(sig['symbol'])} {escape(sig['trade_mode'])} — setup forming {d}</b>\n"
            f"Near {price(snap.get('close'))} · score {float(sig['score']):.2f}\n"
            f"Watching for: {escape('; '.join(need) or 'final confirmation')}.\n"
            f"<i>No trade yet.</i>")


def cancelled(sig, prev_direction: str | None) -> str:
    now = "watching" if sig["tier"] == "WATCHING" else f"{sig['tier'].lower()} {sig['direction'] or ''}".strip()
    return (f"{EMOJI['CANCELLED']} <b>{escape(sig['symbol'])} {escape(sig['trade_mode'])}</b> — the confirmed "
            f"{prev_direction or ''} signal is no longer confirmed (now {escape(now)}). "
            "If you're in the trade, manage it with its stop and target.")


def outcome(o) -> str:
    r = float(o["r_multiple"]) if pd.notna(o["r_multiple"]) else 0.0
    what = {"WIN": "target hit", "LOSS": "stop hit", "TIMEOUT": "time limit reached"}.get(o["outcome"], "")
    return (f"{EMOJI.get(o['outcome'], '')} <b>{escape(o['symbol'])} {escape(o['trade_mode'])} {o['direction']} "
            f"(signal #{o['signal_id']}): {o['outcome']} {r:+.2f}R</b> — {what} at {price(o['resolved_price'])}, "
            f"{_when(o['resolved_at'])}")


def stale_note(n: int) -> str:
    return (f"{EMOJI['INFO']} {n} older tier change(s) weren't announced — they happened a while ago "
            "(Core was catching up after downtime). `python main.py status` in quantora_core shows the current state.")


def health(text: str, since=None, repeat: bool = False) -> str:
    tail = f"\n<i>Still happening — first noticed {_when(since)}.</i>" if repeat and since else ""
    return f"{EMOJI['HEALTH']} <b>Quantora health</b>: {escape(text)}{tail}"


def recovered(text: str) -> str:
    return f"{EMOJI['RECOVERED']} <b>Recovered</b>: {escape(text)}"


def hello(pairs: list[str]) -> str:
    return ("👋 <b>Quantora Notify is connected.</b>\n"
            f"Watching: {escape(', '.join(pairs) or 'no pairs yet (start Core)')}.\n"
            "You'll get Tier 2 heads-ups 🟡, Tier 3 signals 🟢, results ✅❌⏱ and health warnings ⚠️.")
