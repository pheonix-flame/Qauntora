"""End-to-end integration test: every engine, through its real CLI, working together.

    python integration/run_pipeline.py --store files     # no database (Feed --dry-run CSVs)
    python integration/run_pipeline.py --store oracle    # real Oracle (uses ORACLE_* from .env)
                                                          # !! drops and recreates all qtx_ tables !!

Pipeline (each step is a separate process, as on your PC):
  1  Feed      init-db (oracle) + backfill 400 days of V75 from a simulated MT5
  2  Feed      verify — data integrity + cross-check against the (simulated) broker
  3  Core      once --as-of 120 days ago, then once now -> catches up 120 days of tier changes
  4  Core      status
  5  Validate  once + report — labels every Tier 3 / Tier 2 entry
  6  Core      replay -> signals file
  7  Backtest  run --strategy core_signals, zero cost (for the consistency check) and with costs
Then cross-engine consistency checks:
  A  Core live log == Core replay   (live engine and history replay make identical decisions)
  B  Validate covered every entry   (every label belongs to a real Tier 3 / Tier 2 entry)
  C  Validate labels == Backtest    (two independent triple-barrier implementations agree)
  D  Re-runs don't duplicate        (Feed, Core, Validate, Notify run again: nothing doubled)
  E  Notify announced the right things (over real HTTP to a stand-in Telegram server)
  F  Notify health alert + recovery  (pretend Feed went quiet for 5 h, then came back)

Engines never import each other; this script only runs their CLIs and reads their outputs.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pandas as pd

from fake_telegram import FakeTelegram

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
RESULTS: list[tuple[str, bool, str]] = []


def step(name: str, cwd: str, args: list[str], env: dict, ok_codes=(0,), show_tail: int = 6) -> str:
    t = time.time()
    proc = subprocess.run([PY, *args], cwd=ROOT / cwd, env=env, capture_output=True, text=True)
    out = proc.stdout + proc.stderr
    passed = proc.returncode in ok_codes
    RESULTS.append((name, passed, f"exit {proc.returncode}, {time.time() - t:.1f}s"))
    print(f"\n[{'ok' if passed else 'FAIL'}] {name}  ({cwd}: {' '.join(args)})")
    lines = [ln for ln in out.strip().splitlines() if ln.strip()]
    for ln in (lines if not passed else lines[-show_tail:]):
        print("      " + ln)
    if not passed:
        raise SystemExit(f"\nStopped: step '{name}' failed.")
    return out


def check(name: str, passed: bool, detail: str) -> None:
    RESULTS.append((name, passed, detail))
    print(f"\n[{'ok' if passed else 'FAIL'}] {name}: {detail}")


def oracle_conn():
    import oracledb
    sys.path.insert(0, str(ROOT / "quantora_core"))
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    return oracledb.connect(user=os.getenv("ORACLE_USER", "quantora"), password=os.getenv("ORACLE_PASSWORD"),
                            dsn=os.getenv("ORACLE_DSN", "localhost:1521/FREEPDB1"))


def reset_oracle() -> None:
    conn = oracle_conn()
    cur = conn.cursor()
    cur.execute("SELECT table_name FROM user_tables WHERE table_name LIKE 'QTX\\_%' ESCAPE '\\'")
    tables = [r[0] for r in cur.fetchall()]
    order = ["QTX_SIGNAL_OUTCOMES", "QTX_TRADES"]  # children first (foreign keys)
    for t in order + [t for t in tables if t not in order]:
        if t in tables:
            cur.execute(f"DROP TABLE {t} PURGE")
    conn.close()


def load_live(store: str, work: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(Core's signal log, Validate's outcomes) from wherever they were written."""
    if store == "files":
        sig = pd.read_csv(work / "core" / "signals.csv")
        out_path = work / "validate" / "signal_outcomes.csv"
        out = pd.read_csv(out_path) if out_path.exists() else pd.DataFrame(columns=["signal_id", "outcome", "r_multiple"])
    else:
        conn = oracle_conn()
        cur = conn.cursor()
        cur.execute("SELECT signal_id, tier, direction, indicator_snapshot_json FROM qtx_signals ORDER BY signal_id")
        rows = [(a, b, c, d if isinstance(d, dict) else json.loads(d.read() if hasattr(d, "read") else d))
                for a, b, c, d in cur.fetchall()]
        sig = pd.DataFrame(rows, columns=["signal_id", "tier", "direction", "snapshot"])
        cur.execute("SELECT signal_id, outcome, r_multiple, resolved_at FROM qtx_signal_outcomes")
        out = pd.DataFrame(cur.fetchall(), columns=["signal_id", "outcome", "r_multiple", "resolved_at"])
        conn.close()
    snaps = sig["snapshot"] if "snapshot" in sig else sig["indicator_snapshot_json"].map(json.loads)
    sig["close_ts"] = pd.to_datetime(snaps.map(lambda s: s.get("close_ts")))
    sig["prev_tier"] = snaps.map(lambda s: s.get("prev_tier"))
    sig["prev_direction"] = snaps.map(lambda s: s.get("prev_direction"))
    return sig, out


def bar_count(store: str, feed_csv: Path) -> int:
    if store == "files":
        return len(pd.read_csv(feed_csv / "ohlcv_V75_M15.csv"))
    conn = oracle_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM qtx_ohlcv")
    n = cur.fetchone()[0]
    conn.close()
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", choices=["files", "oracle"], default="files")
    ap.add_argument("--workdir", help="keep outputs here (default: a temp folder, deleted afterwards)")
    a = ap.parse_args()
    work = Path(a.workdir) if a.workdir else Path(tempfile.mkdtemp(prefix="qtx_pipeline_"))
    work.mkdir(parents=True, exist_ok=True)
    files = a.store == "files"
    feed_csv = work / "feed"

    env = {**os.environ, "NEWS_PROVIDER": "none", "QTX_SYMBOLS": "V75",
           "FEED_PREVIEW_DIR": str(feed_csv), "FEED_LOG_DIR": str(work / "logs"),
           "CORE_OUTPUT_DIR": str(work / "core"), "VALIDATE_OUTPUT_DIR": str(work / "validate"),
           "BACKTEST_OUTPUT_DIR": str(work / "backtest")}
    tg = FakeTelegram()  # Notify delivers over real HTTP to this local stand-in for api.telegram.org
    no_proxy = ",".join(x for x in (os.environ.get("NO_PROXY", ""), "127.0.0.1", "localhost") if x)
    env.update(TELEGRAM_BOT_TOKEN="999:FAKE-integration-token", TELEGRAM_CHAT_ID="4242",
               TELEGRAM_API_BASE=tg.url, NOTIFY_STATE_DIR=str(work / "notify"), NO_PROXY=no_proxy, no_proxy=no_proxy)
    notify_src = ["--csv-dir", str(feed_csv), "--core-dir", str(work / "core"),
                  "--validate-dir", str(work / "validate")] if files else []
    local_feed = ["--dry-run"] if files else []
    core_src = ["--csv-dir", str(feed_csv)] if files else []
    val_src = ["--csv-dir", str(feed_csv), "--core-dir", str(work / "core")] if files else []
    bt_src = ["--csv-dir", str(feed_csv)] if files else []

    # settings copies for the test run (your real settings files are not touched)
    core_settings = work / "core_settings.toml"
    core_settings.write_text((ROOT / "quantora_core/settings.toml").read_text().replace(
        "max_catch_up_bars = 50", "max_catch_up_bars = 2000"))
    bt_text = (ROOT / "quantora_backtest/settings.toml").read_text()
    bt_free, bt_cost = work / "bt_free.toml", work / "bt_cost.toml"
    bt_free.write_text(bt_text.replace("[costs]\n", "[costs]\nV75 = { spread = 0.0, slippage = 0.0 }\n", 1))
    bt_cost.write_text(bt_text.replace("[costs]\n", "[costs]\nV75 = { spread = 40.0, slippage = 20.0 }\n", 1))

    print(f"Quantora integration test — store: {a.store} — work folder: {work}")
    if not files:
        print("Resetting qtx_ tables in Oracle ...")
        reset_oracle()
        step("Feed: create schema", "quantora_feed", ["../integration/fake_mt5_feed.py", "init-db"], env)

    # 1-2 Feed
    step("Feed: backfill 400 days", "quantora_feed",
         ["../integration/fake_mt5_feed.py", "backfill", "--days", "400", *local_feed], env)
    step("Feed: verify data", "quantora_feed",
         ["../integration/fake_mt5_feed.py", "verify", "--days", "30", "--no-reference", *local_feed], env, show_tail=4)
    step("Notify: first connection", "quantora_notify", ["main.py", "once", *notify_src], env, show_tail=2)
    hello = tg.messages()

    # 3-4 Core (live, with catch-up)
    now = pd.Timestamp.now("UTC").tz_localize(None)
    as_of = (now - pd.Timedelta(days=120)).strftime("%Y-%m-%d %H:%M")
    step("Core: evaluate as of 120 days ago", "quantora_core",
         ["main.py", "--settings", str(core_settings), "once", "--as-of", as_of, *core_src], env, show_tail=3)
    step("Core: evaluate now (catch-up)", "quantora_core",
         ["main.py", "--settings", str(core_settings), "once", *core_src], env, show_tail=3)
    step("Core: status", "quantora_core", ["main.py", "status", *core_src], env, show_tail=2)

    # 5 Validate
    step("Validate: label outcomes", "quantora_validate", ["main.py", "once", *val_src], env)
    step("Validate: report", "quantora_validate", ["main.py", "report", *val_src], env, show_tail=8)

    # 5b Notify announces what's new since it connected
    n_before = len(tg.messages())
    notify_now = pd.Timestamp.now("UTC").tz_localize(None)
    step("Notify: announce new signals", "quantora_notify", ["main.py", "once", *notify_src], env, show_tail=2)
    announced = tg.messages()[n_before:]

    # 6-7 Replay + Backtest
    replay_csv = work / "replay_V75_SWING.csv"
    step("Core: replay history", "quantora_core",
         ["main.py", "replay", "--symbol", "V75", "--mode", "SWING", "--out", str(replay_csv), *core_src], env,
         show_tail=2)
    bt_args = ["run", "--symbol", "V75", "--mode", "SWING", "--strategy", "core_signals", "--signals", str(replay_csv),
               "--runs", "40", *bt_src]
    step("Backtest: check data", "quantora_backtest", ["main.py", "--settings", str(bt_cost), "check", *bt_src], env,
         show_tail=3)
    step("Backtest: Core signals, zero cost", "quantora_backtest", ["main.py", "--settings", str(bt_free), *bt_args],
         env, ok_codes=(0, 1), show_tail=3)
    free_trades = sorted((work / "backtest").glob("*_trades.csv"))[-1]
    out = step("Backtest: Core signals, with costs", "quantora_backtest",
               ["main.py", "--settings", str(bt_cost), *bt_args], env, ok_codes=(0, 1), show_tail=12)

    # A — live == replay
    sig, outcomes = load_live(a.store, work)
    replay = pd.read_csv(replay_csv, parse_dates=["signal_ts"])
    live = sig.iloc[1:]  # first row = state found at the as-of start, not a fresh transition
    rep = replay[(replay["signal_ts"] >= live["close_ts"].min()) & (replay["signal_ts"] <= live["close_ts"].max())]
    norm = lambda t, d: (t, d if t != "WATCHING" and isinstance(d, str) else None)  # noqa: E731
    got = [(ts, *norm(t, d)) for ts, t, d in zip(live["close_ts"], live["tier"], live["direction"])]
    want = [(ts, *norm(t, d)) for ts, t, d in zip(rep["signal_ts"], rep["tier"], rep["direction"])]
    check("A  Core live log == Core replay", got == want and len(got) > 10,
          f"{len(got)} live tier changes vs {len(want)} in replay over the same 120 days"
          + ("" if got == want else f"; first difference at #{next(i for i, (x, y) in enumerate(zip(got, want)) if x != y) if any(x != y for x, y in zip(got, want)) else min(len(got), len(want))}"))

    # B — Validate covered everything
    flip = sig["prev_direction"].notna() & sig["prev_direction"].ne(sig["direction"])
    entries = sig[sig["direction"].isin(["BUY", "SELL"]) & (sig["tier"].eq("CONFIRMED") | (
        sig["tier"].eq("FORMING") & (sig["prev_tier"].eq("WATCHING") | flip)))]
    unknown = set(outcomes["signal_id"]) - set(sig["signal_id"])
    not_entries = set(outcomes["signal_id"]) - set(entries["signal_id"])
    labelled_conf = outcomes.merge(sig, on="signal_id")
    check("B  Validate labelled Core's signals", not unknown and not not_entries and len(outcomes) > 0,
          f"{len(outcomes)} of {len(entries)} entries labelled "
          f"({int((labelled_conf['tier'] == 'CONFIRMED').sum())} Tier 3, "
          f"{int((labelled_conf['tier'] == 'FORMING').sum())} Tier 2); the rest are still open; "
          f"{len(unknown)} outcomes point at unknown signals, {len(not_entries)} at non-entries")

    # C — two independent triple-barrier implementations agree
    bt = pd.read_csv(free_trades, parse_dates=["signal_ts"])
    conf = labelled_conf[labelled_conf["tier"] == "CONFIRMED"][["close_ts", "outcome", "r_multiple"]]
    m = conf.merge(bt[["signal_ts", "outcome", "r"]], left_on="close_ts", right_on="signal_ts",
                   suffixes=("_validate", "_backtest"))
    agree = (m["outcome_validate"] == m["outcome_backtest"]) & ((m["r_multiple"] - m["r"]).abs() < 0.01)
    check("C  Validate labels == Backtest outcomes", len(m) >= 5 and agree.all(),
          f"{int(agree.sum())}/{len(m)} shared Tier 3 trades have the same outcome and R "
          "(zero-cost backtest; trades only one engine took are excluded)")

    # D — scheduled re-runs must not duplicate anything
    before = (len(load_live(a.store, work)[0]), len(load_live(a.store, work)[1]), bar_count(a.store, feed_csv))
    step("Feed: incremental run again", "quantora_feed", ["../integration/fake_mt5_feed.py", "once", *local_feed],
         env, show_tail=2)
    step("Core: evaluate again", "quantora_core", ["main.py", "once", *core_src], env, show_tail=1)
    step("Validate: label again", "quantora_validate", ["main.py", "once", *val_src], env, show_tail=1)
    n_rerun = len(tg.messages())
    step("Notify: run again", "quantora_notify", ["main.py", "once", *notify_src], env, show_tail=1)
    notify_dupes = len(tg.messages()) - n_rerun
    sig2, out2 = load_live(a.store, work)
    after = (len(sig2), len(out2), bar_count(a.store, feed_csv))
    new_bars = after[2] - before[2]
    check("D  Re-runs don't duplicate", after[0] == before[0] and after[1] >= before[1]
          and out2["signal_id"].is_unique and 0 <= new_bars <= 2 and notify_dupes == 0,
          f"signals {before[0]}->{after[0]}, outcomes {before[1]}->{after[1]} (unique ids), "
          f"price bars +{new_bars} (only bars that closed meanwhile), Notify messages +{notify_dupes}")

    # E — Notify said exactly what it should, to the right chat, with the right token
    def expected_kind(r):
        flip = isinstance(r.prev_direction, str) and r.prev_direction != r.direction
        if r.tier == "CONFIRMED":
            return "tier3"
        if r.tier == "FORMING" and r.direction in ("BUY", "SELL") and (r.prev_tier == "WATCHING" or flip):
            return "tier2"
        if r.prev_tier == "CONFIRMED":
            return "cancelled"
        return None
    kinds = [(expected_kind(r), notify_now - r.close_ts <= pd.Timedelta(hours=8)) for r in sig.itertuples()]
    fresh = sum(1 for k, f in kinds if k and f)
    stale = sum(1 for k, f in kinds if k and not f)
    oc = outcomes.merge(sig[["signal_id", "tier"]], on="signal_id")
    fresh_out = int(((oc["tier"] == "CONFIRMED") &
                     (notify_now - pd.to_datetime(oc["resolved_at"]) <= pd.Timedelta(hours=24))).sum())
    want_n = fresh + (1 if stale else 0) + fresh_out
    right_dest = all(m["token"] == "999:FAKE-integration-token" and m["body"]["chat_id"] == "4242"
                     and m["body"]["parse_mode"] == "HTML" for m in tg.messages())
    check("E  Notify announced the right things", len(hello) == 1 and "connected" in hello[0]["body"]["text"]
          and len(announced) == want_n and right_dest,
          f"hello on first run; then {len(announced)} messages = {fresh} fresh signal alert(s) + "
          f"{'1 note for ' + str(stale) + ' old catch-up changes' if stale else 'no stale note'} + "
          f"{fresh_out} result(s); all to chat 4242 with the right token")

    # F — health alert when Feed goes quiet, recovery when it's back
    n_h = len(tg.messages())
    later = (pd.Timestamp.now("UTC").tz_localize(None) + pd.Timedelta(hours=5)).strftime("%Y-%m-%d %H:%M")
    step("Notify: 5 h later, Feed silent", "quantora_notify", ["main.py", "once", "--as-of", later, *notify_src],
         env, show_tail=1)
    alert = [m["body"]["text"] for m in tg.messages()[n_h:]]
    step("Notify: back to now", "quantora_notify", ["main.py", "once", *notify_src], env, show_tail=1)
    rec = [m["body"]["text"] for m in tg.messages()[n_h + len(alert):]]
    check("F  Notify health alert + recovery",
          len(alert) == 1 and "Feed hasn't stored a V75" in alert[0] and len(rec) == 1 and "Recovered" in rec[0],
          f"alert: {alert[0][:70] if alert else 'none'}…; then: {rec[0][:60] if rec else 'none'}…")
    tg.stop()

    gate = re.findall(r"\[(pass|FAIL|----)\] (.+?)\s{2,}(.+)", out)
    print("\nBacktest gate on simulated data (informational — simulated prices have no real edge):")
    for mark, rule, detail in gate:
        print(f"   [{mark}] {rule}: {detail}")

    print("\n" + "=" * 72)
    failed = [r for r in RESULTS if not r[1]]
    for name, ok, detail in RESULTS:
        print(f"  [{'ok  ' if ok else 'FAIL'}] {name:42} {detail}")
    print("=" * 72)
    print(f"{'ALL ENGINES WORK TOGETHER' if not failed else f'{len(failed)} FAILURE(S)'} "
          f"— {len(RESULTS)} checks, store: {a.store}")
    if not a.workdir:
        shutil.rmtree(work, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
