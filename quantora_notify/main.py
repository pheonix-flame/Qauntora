"""Quantora Notify — command-line entry point.

Setting up the bot (once):
    python main.py chat-id      # after messaging your bot: prints your TELEGRAM_CHAT_ID
    python main.py test         # sends a test message to that chat
    python main.py check        # token, chat, database, what's being watched

Running:
    python main.py once         # one pass: health checks, new signals, new outcomes
    python main.py run          # every interval_seconds, forever

Options for once/run: --channel console (print instead of sending — try it before
the bot exists); local files instead of Oracle: --csv-dir ../quantora_feed/preview_output
--core-dir ../quantora_core/core_output --validate-dir ../quantora_validate/validate_output
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from channels import DeliveryError, build_channel
from config import NotifyConfig, load_config
from notifier import run_pass
from storage import open_reader

log = logging.getLogger("quantora.notify")


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def state_path(cfg: NotifyConfig, channel: str) -> Path:
    # separate positions per channel, so trying --channel console never "uses up" Telegram alerts
    return cfg.state_dir / f"state_{channel}.json"


def cmd_chat_id(cfg: NotifyConfig, a) -> int:
    from channels.telegram import TelegramChannel
    try:
        tg = TelegramChannel(cfg.telegram)
        me = tg.get_me()
        chats = tg.recent_chats()
    except (ValueError, DeliveryError) as exc:
        print(f"FAILED: {exc}")
        return 1
    print(f"Bot: @{me.get('username')} ({me.get('first_name')})")
    if not chats:
        print(f"\nNo messages found yet. Open Telegram, search for @{me.get('username')}, press START,\n"
              "send it any message (e.g. 'hi'), then run this command again.")
        return 1
    print("\nChats that messaged the bot recently:")
    for c in chats:
        print(f"  TELEGRAM_CHAT_ID={c['id']}    ({c['type']}: {c['name']})")
    print("\nCopy the line for YOUR chat into Qauntora/.env, then run: python main.py test")
    return 0


def cmd_test(cfg: NotifyConfig, a) -> int:
    try:
        ch = build_channel(cfg, a.channel)
        ch.send("✅ <b>Quantora test message</b> — if you can read this, Notify can reach you. "
                f"Sent {utc_now():%d %b %H:%M} UTC.")
    except (ValueError, DeliveryError) as exc:
        print(f"FAILED: {exc}")
        return 1
    print(f"Sent via {a.channel}. Check your Telegram." if a.channel == "telegram" else "Printed above.")
    return 0


def cmd_check(cfg: NotifyConfig, a) -> int:
    ok = True
    print("== Telegram ==")
    t = cfg.telegram
    print(f"TELEGRAM_BOT_TOKEN: {'set' if t.token else 'MISSING — see README: Create your Telegram bot'}")
    print(f"TELEGRAM_CHAT_ID:   {t.chat_id or 'MISSING — run: python main.py chat-id'}")
    if t.token:
        try:
            from channels.telegram import TelegramChannel
            me = TelegramChannel(t).get_me()
            print(f"Bot reachable: @{me.get('username')}")
        except DeliveryError as exc:
            ok = False
            print(f"FAILED: {exc}")
    ok = ok and bool(t.token and t.chat_id)
    print("\n== Data ==")
    try:
        with open_reader(cfg, a.csv_dir, a.core_dir, a.validate_dir) as r:
            print(r.describe())
            pairs = r.core_states()
            print(f"Pairs Core is running: {', '.join(f'{p.symbol} {p.trade_mode}' for p in pairs.itertuples()) or 'none yet'}")
            print(f"Signals so far: {r.max_signal_id()}, outcomes so far: {r.max_outcome_id()}")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"FAILED: {exc}")
    print(f"\nState file: {state_path(cfg, 'telegram')}")
    print("\nAll good." if ok else "\nFix the items above, then run `python main.py test`.")
    return 0 if ok else 1


def one_pass(cfg: NotifyConfig, a, now: datetime):
    channel = build_channel(cfg, a.channel)
    return run_pass(cfg, lambda: open_reader(cfg, a.csv_dir, a.core_dir, a.validate_dir), channel,
                    state_path(cfg, a.channel), now)


def cmd_once(cfg: NotifyConfig, a) -> int:
    now = pd.Timestamp(a.as_of).to_pydatetime() if a.as_of else utc_now()
    try:
        rep = one_pass(cfg, a, now)
    except ValueError as exc:
        print(f"FAILED: {exc}")
        return 1
    kinds = pd.Series(rep.sent).value_counts().to_dict() if rep.sent else {}
    print(f"\nSent {len(rep.sent)} message(s){': ' + ', '.join(f'{k} {v}' for k, v in kinds.items()) if kinds else ''}"
          + (f"; {rep.stale_skipped} old tier change(s) not announced" if rep.stale_skipped else ""))
    if rep.error:
        print(f"DELIVERY FAILED (will retry next pass): {rep.error}")
        return 1
    return 0


def cmd_run(cfg: NotifyConfig, a) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    log.info("Notify running via %s, every %ds", a.channel, cfg.interval_seconds)
    try:
        while True:
            try:
                rep = one_pass(cfg, a, utc_now())
                if rep.sent:
                    log.info("sent %d message(s)", len(rep.sent))
            except Exception:  # noqa: BLE001
                log.exception("Notify pass failed; retrying")
            time.sleep(cfg.interval_seconds)
    except KeyboardInterrupt:
        return 0


def main(argv: list[str] | None = None) -> int:
    # Windows writes piped/redirected output in cp1252, which can't encode → × — or emoji: force UTF-8.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="Quantora Notify — Telegram trading + health alerts")
    p.add_argument("--settings", help="use a different settings file")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("chat-id", help="find your TELEGRAM_CHAT_ID (message the bot first)")
    for name in ("test", "check", "once", "run"):
        sp = sub.add_parser(name)
        if name != "check":
            sp.add_argument("--channel", default="telegram", choices=["telegram", "console"])
        if name != "test":
            sp.add_argument("--csv-dir")
            sp.add_argument("--core-dir")
            sp.add_argument("--validate-dir")
        if name == "once":
            sp.add_argument("--as-of", help="pretend it's this UTC time (testing health alerts)")
    a = p.parse_args(argv)
    cfg = load_config(Path(a.settings) if a.settings else None)
    return {"chat-id": cmd_chat_id, "test": cmd_test, "check": cmd_check,
            "once": cmd_once, "run": cmd_run}[a.cmd](cfg, a)


if __name__ == "__main__":
    sys.exit(main())
