"""One Notify pass: health first, then trading alerts, then outcomes."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

import pandas as pd

import health
import messages as msg
from channels import Channel, DeliveryError
from config import MODE_BAR_MINUTES, NotifyConfig
from state import NotifyState
from storage import NotifyReader

log = logging.getLogger("quantora.notify")
MAX_MESSAGES_PER_PASS = 30  # never flood the chat; anything left goes next pass


@dataclass
class PassReport:
    sent: list[str] = field(default_factory=list)   # message kinds, in order
    stale_skipped: int = 0
    error: str = ""


def is_tier2_entry(sig) -> bool:
    snap = sig["snapshot"] or {}
    prev_dir = snap.get("prev_direction")
    return sig["tier"] == "FORMING" and sig["direction"] in ("BUY", "SELL") and (
        snap.get("prev_tier") == "WATCHING" or (prev_dir is not None and prev_dir != sig["direction"]))


def classify(sig, cfg: NotifyConfig) -> str | None:
    snap = sig["snapshot"] or {}
    if sig["tier"] == "CONFIRMED" and sig["direction"] in ("BUY", "SELL"):
        return "tier3" if cfg.tier3 else None
    if is_tier2_entry(sig):
        return "tier2" if cfg.tier2 else None
    if snap.get("prev_tier") == "CONFIRMED" and sig["tier"] != "CONFIRMED":
        return "cancelled" if cfg.cancelled else None
    return None


def run_pass(cfg: NotifyConfig, open_reader: Callable[[], NotifyReader], channel: Channel, state_path: Path,
             now: datetime) -> PassReport:
    rep = PassReport()
    st = NotifyState.load(state_path)
    budget = [MAX_MESSAGES_PER_PASS]

    def send(kind: str, text: str) -> bool:
        if budget[0] <= 0:
            return False
        channel.send(text)          # raises DeliveryError -> handled below
        budget[0] -= 1
        rep.sent.append(kind)
        return True

    try:
        try:
            reader = open_reader()
        except Exception as exc:  # noqa: BLE001 — the DB itself is down: say so, even without data
            _health_round(st, {"database": f"Can't reach the database: {str(exc)[:200]}. Is Oracle running?"},
                          cfg, now, send)
            st.save(state_path)
            return rep
        with reader:
            _health_round(st, health.check(reader, cfg, now), cfg, now, send, clear_db=True)
            st.save(state_path)

            if not st.initialized:  # first run: start from "now", don't replay history into the chat
                st.last_signal_id, st.last_outcome_id = reader.max_signal_id(), reader.max_outcome_id()
                pairs = [f"{r.symbol} {r.trade_mode}" for r in reader.core_states().itertuples(index=False)]
                send("hello", msg.hello(pairs))
                st.initialized = True
                st.save(state_path)
                return rep

            stale = 0
            for _, sig in reader.signals_after(st.last_signal_id).iterrows():
                kind = classify(sig, cfg)
                if kind:
                    close_ts = (sig["snapshot"] or {}).get("close_ts")
                    max_age = timedelta(minutes=cfg.max_signal_age_bars * MODE_BAR_MINUTES.get(sig["trade_mode"], 240))
                    if close_ts and pd.Timestamp(now) - pd.Timestamp(close_ts) > max_age:
                        stale += 1
                    else:
                        text = {"tier3": msg.tier3, "tier2": msg.tier2}.get(kind)
                        text = text(sig) if text else msg.cancelled(sig, (sig["snapshot"] or {}).get("prev_direction"))
                        if not send(kind, text):
                            break
                st.last_signal_id = int(sig["signal_id"])
                st.save(state_path)
            if stale:
                rep.stale_skipped = stale
                send("stale_note", msg.stale_note(stale))

            for _, o in reader.outcomes_after(st.last_outcome_id).iterrows():
                fresh = pd.Timestamp(now) - pd.Timestamp(o["resolved_at"]) <= timedelta(hours=cfg.max_outcome_age_hours)
                if cfg.outcomes and o["tier"] == "CONFIRMED" and fresh:
                    if not send("outcome", msg.outcome(o)):
                        break
                st.last_outcome_id = int(o["outcome_id"])
                st.save(state_path)
    except DeliveryError as exc:
        rep.error = str(exc)
        log.error("Delivery failed (will retry next pass): %s", exc)
    st.save(state_path)
    return rep


def _health_round(st: NotifyState, problems: dict, cfg: NotifyConfig, now, send, clear_db: bool = False) -> None:
    now_s = pd.Timestamp(now).isoformat()
    repeat = timedelta(hours=cfg.repeat_every_hours)
    for key, text in problems.items():
        known = st.problems.get(key)
        if known is None:
            if send("health", msg.health(text)):
                st.problems[key] = {"text": text, "first_seen": now_s, "last_sent": now_s}
        elif pd.Timestamp(now) - pd.Timestamp(known["last_sent"]) >= repeat:
            if send("health_repeat", msg.health(text, since=known["first_seen"], repeat=True)):
                known.update(text=text, last_sent=now_s)
    if not clear_db:  # the database is down: nothing else could be checked, so nothing else "recovered"
        return
    labels = {"feed": "Feed is storing {} bars again.", "core": "Core is evaluating {} again.",
              "validate": "Validate is labelling overdue signals again.", "database": "The database is reachable again."}
    for key in [k for k in st.problems if k not in problems]:
        kind, _, what = key.partition(":")
        if send("recovered", msg.recovered(labels[kind].format(what.replace("|", " ")))):
            del st.problems[key]
