from dataclasses import replace

import pandas as pd
import pytest
import requests

import health
import messages as msg
from channels.base import DeliveryError
from channels.console import ConsoleChannel
from channels.telegram import TelegramChannel
from config import TelegramConfig, load_config
from notifier import classify, run_pass
from state import NotifyState
from storage.base import NotifyReader

TOKEN = "123456:SECRET-token-abc"
NOW = pd.Timestamp("2026-11-04 12:10")  # a Wednesday


def channel(tg, chat="777"):
    s = requests.Session()
    s.trust_env = False  # talk to the local fake directly, not via any proxy
    return TelegramChannel(TelegramConfig(TOKEN, chat, tg.url), session=s, sleep=lambda *_: None)


# -- Telegram channel over real HTTP -------------------------------------------------------
def test_send_posts_html_to_the_chat(tg):
    channel(tg).send("<b>hi</b>")
    c = tg.calls[-1]
    assert c["token"] == TOKEN and c["method"] == "sendMessage"
    assert c["body"] == {"chat_id": "777", "text": "<b>hi</b>", "parse_mode": "HTML", "disable_web_page_preview": True}


def test_wrong_token_is_explained_and_never_leaked(tg):
    tg.script = [(401, {"ok": False, "description": f"Unauthorized for {TOKEN}"})]
    with pytest.raises(DeliveryError) as e:
        channel(tg).send("x")
    assert "token is wrong" in str(e.value) and TOKEN not in str(e.value)


def test_chat_not_found_hint(tg):
    tg.script = [(400, {"ok": False, "description": "Bad Request: chat not found"})]
    with pytest.raises(DeliveryError, match="press.*Start|Start"):
        channel(tg).send("x")


def test_rate_limit_is_waited_out(tg):
    tg.script = [(429, {"ok": False, "parameters": {"retry_after": 1}})]
    channel(tg).send("x")
    assert len(tg.messages()) == 2  # retried once, then delivered


def test_network_error_never_leaks_token():
    ch = TelegramChannel(TelegramConfig(TOKEN, "1", "http://127.0.0.1:9"), sleep=lambda *_: None)
    ch.http.trust_env = False
    with pytest.raises(DeliveryError) as e:
        ch.send("x")
    assert TOKEN not in str(e.value) and "<token>" in str(e.value)


def test_long_messages_are_split(tg):
    channel(tg).send("\n".join(["line " + "x" * 100] * 100))
    assert len(tg.messages()) == 3 and all(len(m) <= 4096 for m in tg.messages())


def test_chat_id_discovery(tg):
    tg.updates = [{"message": {"chat": {"id": 42, "type": "private", "first_name": "Austin"}}},
                  {"message": {"chat": {"id": 42, "type": "private", "first_name": "Austin"}}}]
    assert channel(tg).recent_chats() == [{"id": 42, "type": "private", "name": "Austin"}]


def test_missing_token_or_chat():
    with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
        TelegramChannel(TelegramConfig("", "1"))
    with pytest.raises(DeliveryError, match="chat-id"):
        TelegramChannel(TelegramConfig(TOKEN, "")).send("x")


# -- what gets announced -----------------------------------------------------------------------
def sig(sid, tier, direction, close_ts=NOW - pd.Timedelta(minutes=10), prev="WATCHING", prev_dir=None, **cols):
    snap = {"close_ts": str(close_ts), "close": 400000.0, "adx": 17.5, "rsi": 61.0, "prev_tier": prev,
            "prev_direction": prev_dir, "conditions": {"trend": True, "strength": False, "momentum": True,
                                                       "location": True}, "trigger": False, "chase": False}
    base = {"signal_id": sid, "symbol": "V75", "trade_mode": "SWING", "tier": tier, "direction": direction,
            "score": 0.75, "entry_price": None, "stop_loss": None, "take_profit": None, "snapshot": snap}
    return base | cols


def test_classification():
    cfg = load_config()
    s = lambda **k: pd.Series(sig(1, **k))  # noqa: E731
    assert classify(s(tier="CONFIRMED", direction="BUY", prev="FORMING", prev_dir="BUY"), cfg) == "tier3"
    assert classify(s(tier="FORMING", direction="BUY"), cfg) == "tier2"
    assert classify(s(tier="FORMING", direction="SELL", prev="FORMING", prev_dir="BUY"), cfg) == "tier2"  # flip
    assert classify(s(tier="FORMING", direction="BUY", prev="CONFIRMED", prev_dir="BUY"), cfg) == "cancelled"
    assert classify(s(tier="WATCHING", direction="BUY", prev="FORMING", prev_dir="BUY"), cfg) is None
    assert classify(s(tier="FORMING", direction="BUY"), replace(cfg, tier2=False)) is None


def test_message_texts_are_safe_and_specific():
    t2 = msg.tier2(pd.Series(sig(1, "FORMING", "BUY")))
    assert "Watching for" in t2 and "ADX now 17.5" in t2 and "confirmed bullish close" in t2 and "No trade yet" in t2
    hot = pd.Series(sig(9, "FORMING", "BUY")); hot["snapshot"] = hot["snapshot"] | {"rsi": 82.5, "conditions": {"momentum": False}}
    assert "overbought (now 82.5)" in msg.tier2(hot)
    t3 = msg.tier3(pd.Series(sig(2, "CONFIRMED", "SELL", entry_price=400000.0, stop_loss=401500.0,
                                 take_profit=397000.0)))
    assert "CONFIRMED SELL" in t3 and "400,000.00" in t3 and "2.0:1" in t3
    evil = pd.Series(sig(3, "FORMING", "BUY") | {"symbol": "<script>"})
    assert "<script>" not in msg.tier2(evil) and "&lt;script&gt;" in msg.tier2(evil)


# -- a full pass against an in-memory database -----------------------------------------------------
class FakeReader(NotifyReader):
    def __init__(self, signals=(), outcomes=(), feed_last=NOW - pd.Timedelta(minutes=20),
                 core_updated=NOW - pd.Timedelta(minutes=10), unlabelled=()):
        self.sig = pd.DataFrame(list(signals))
        self.out = pd.DataFrame(list(outcomes))
        self.feed_last, self.core_updated, self.unl = feed_last, core_updated, list(unlabelled)

    def max_signal_id(self):
        return int(self.sig["signal_id"].max()) if len(self.sig) else 0

    def signals_after(self, i, limit=500):
        return self.sig[self.sig["signal_id"] > i].head(limit) if len(self.sig) else self.sig

    def max_outcome_id(self):
        return len(self.out)

    def outcomes_after(self, i, limit=500):
        return self.out[self.out["outcome_id"] > i] if len(self.out) else self.out

    def feed_freshness(self):
        return pd.DataFrame([("V75", "SYNTHETIC", self.feed_last)], columns=["symbol", "asset_class", "last_bar_ts"])

    def core_states(self):
        return pd.DataFrame([("V75", "SWING", "FORMING", self.core_updated)],
                            columns=["symbol", "trade_mode", "tier", "updated_at"])

    def unlabelled_confirmed(self):
        return pd.DataFrame(self.unl, columns=["signal_id", "symbol", "trade_mode", "close_ts"])


@pytest.fixture
def cfg(tmp_path):
    return replace(load_config(), state_dir=tmp_path)


def run(cfg, reader, ch, now=NOW):
    return run_pass(cfg, lambda: reader, ch, cfg.state_dir / "s.json", now)


def outcome_row(oid, sid, tier="CONFIRMED", resolved=NOW - pd.Timedelta(hours=1)):
    return {"outcome_id": oid, "signal_id": sid, "outcome": "WIN", "r_multiple": 2.0, "resolved_price": 1.0,
            "resolved_at": resolved, "symbol": "V75", "trade_mode": "SWING", "tier": tier, "direction": "BUY"}


def test_first_run_says_hello_and_does_not_replay_history(cfg):
    ch = ConsoleChannel()
    rep = run(cfg, FakeReader([sig(1, "CONFIRMED", "BUY"), sig(2, "FORMING", "BUY")], [outcome_row(1, 1)]), ch)
    assert rep.sent == ["hello"]
    st = NotifyState.load(cfg.state_dir / "s.json")
    assert (st.last_signal_id, st.last_outcome_id, st.initialized) == (2, 1, True)


def test_new_signals_and_outcomes_are_announced_once(cfg):
    ch = ConsoleChannel()
    run(cfg, FakeReader(), ch)  # hello
    reader = FakeReader([sig(1, "FORMING", "BUY"),
                         sig(2, "CONFIRMED", "BUY", prev="FORMING", prev_dir="BUY", entry_price=1.0, stop_loss=0.9,
                             take_profit=1.2),
                         sig(3, "FORMING", "BUY", prev="CONFIRMED", prev_dir="BUY"),
                         sig(4, "WATCHING", "BUY", prev="FORMING", prev_dir="BUY")],
                        [outcome_row(1, 2), outcome_row(2, 1, tier="FORMING")])
    assert run(cfg, reader, ch).sent == ["tier2", "tier3", "cancelled", "outcome"]
    assert run(cfg, reader, ch).sent == []  # nothing twice


def test_old_signals_are_not_announced_as_new(cfg):
    ch = ConsoleChannel()
    run(cfg, FakeReader(), ch)
    old = NOW - pd.Timedelta(days=3)
    reader = FakeReader([sig(1, "CONFIRMED", "BUY", close_ts=old), sig(2, "FORMING", "SELL", close_ts=old),
                         sig(3, "FORMING", "BUY")],
                        [outcome_row(1, 1, resolved=NOW - pd.Timedelta(days=2))])
    rep = run(cfg, reader, ch)
    assert rep.sent == ["tier2", "stale_note"] and rep.stale_skipped == 2


def test_failed_delivery_is_retried_not_lost(cfg):
    class Flaky(ConsoleChannel):
        fail = True

        def send(self, text):
            if self.fail and "CONFIRMED" in text:
                raise DeliveryError("network down")
            super().send(text)
    ch = Flaky()
    run(cfg, FakeReader(), ch)
    reader = FakeReader([sig(1, "FORMING", "BUY"), sig(2, "CONFIRMED", "BUY", prev="FORMING", prev_dir="BUY")])
    rep = run(cfg, reader, ch)
    assert rep.sent == ["tier2"] and "network down" in rep.error
    ch.fail = False
    assert run(cfg, reader, ch).sent == ["tier3"]  # the failed one, exactly once


# -- health -------------------------------------------------------------------------------------
def test_health_alert_repeat_and_recovery(cfg):
    ch = ConsoleChannel()
    run(cfg, FakeReader(), ch)
    stale = FakeReader(feed_last=NOW - pd.Timedelta(hours=3), core_updated=NOW - pd.Timedelta(hours=12))
    assert run(cfg, stale, ch).sent == ["health"]                    # Feed only: Core's staleness has the same cause
    assert run(cfg, stale, ch, NOW + pd.Timedelta(hours=1)).sent == []
    assert run(cfg, stale, ch, NOW + pd.Timedelta(hours=3)).sent == ["health_repeat"]
    later = NOW + pd.Timedelta(hours=3)
    fresh = FakeReader(feed_last=later - pd.Timedelta(minutes=20), core_updated=later - pd.Timedelta(minutes=10))
    assert run(cfg, fresh, ch, later).sent == ["recovered"]


def test_core_and_validate_checks():
    cfg = load_config()
    r = FakeReader(core_updated=NOW - pd.Timedelta(hours=9),
                   unlabelled=[(5, "V75", "SWING", str(NOW - pd.Timedelta(days=15)))])
    p = health.check(r, cfg, NOW)
    assert set(p) == {"core:V75|SWING", "validate"} and "quantora_core" in p["core:V75|SWING"]


def test_forex_weekend_is_not_an_outage():
    cfg = load_config()
    saturday = pd.Timestamp("2026-11-07 12:00")
    r = FakeReader(feed_last=pd.Timestamp("2026-11-06 20:45"), core_updated=pd.Timestamp("2026-11-06 20:01"))
    r.feed_freshness = lambda: pd.DataFrame([("EURUSD", "FOREX", pd.Timestamp("2026-11-06 20:45"))],
                                            columns=["symbol", "asset_class", "last_bar_ts"])
    r.core_states = lambda: pd.DataFrame([("EURUSD", "SWING", "WATCHING", pd.Timestamp("2026-11-06 20:01"))],
                                         columns=["symbol", "trade_mode", "tier", "updated_at"])
    assert health.check(r, cfg, saturday) == {}


def test_database_down_then_back(cfg):
    ch = ConsoleChannel()
    run(cfg, FakeReader(), ch)

    def down():
        raise ConnectionError("ORA-12541: no listener")
    rep = run_pass(cfg, down, ch, cfg.state_dir / "s.json", NOW)
    assert rep.sent == ["health"] and "database" in ch.sent[-1].lower()
    assert run(cfg, FakeReader(), ch).sent == ["recovered"]
