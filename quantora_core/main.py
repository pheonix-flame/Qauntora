"""Quantora Core — command-line entry point.

    python main.py check                         # DB, data per symbol, which config each pair uses
    python main.py once                          # evaluate every pair on its latest closed bar, update state
    python main.py run                           # loop: wake after each M15 close, evaluate pairs whose bar closed
    python main.py status                        # current tier of every pair (qtx_signal_state)
    python main.py replay --symbol V75 --mode SWING          # history -> signals CSV for quantora_backtest
    python main.py diagnose --symbol V75 --mode SWING        # collinearity / VIF check (Section 5)

Add --csv-dir ../quantora_feed/preview_output to use Feed's --dry-run CSVs and
local state files instead of Oracle (nothing is written to the database).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pandas as pd

from config import CoreConfig, load_config
from diagnose import collinearity
from engine import evaluate_bars, evaluate_pair, replay, signal_config
from engine import closed_bars
from storage import open_store

log = logging.getLogger("quantora.core")


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def setup_logging(cfg: CoreConfig) -> None:
    log_dir = cfg.output_dir.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    fmt.converter = time.gmtime
    root = logging.getLogger("quantora")
    root.setLevel(logging.INFO)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    fh = RotatingFileHandler(log_dir / "core.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    root.handlers[:] = [console, fh]


def cmd_check(cfg: CoreConfig, csv_dir) -> int:
    print("\n== Store ==")
    try:
        store = open_store(cfg, csv_dir)
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED: {exc}")
        return 1
    ok = True
    with store:
        print(store.describe())
        print(f"\n{'pair':14} {'timeframe':9} {'M15 bars':>9} {'newest bar (UTC)':>18} {'closed bars':>11}  config")
        now = utc_now()
        for sym, mode_name in cfg.pairs():
            mode = cfg.modes[mode_name]
            m15 = store.load_m15(sym, now - timedelta(minutes=mode.bar_minutes * (mode.lookback_bars + 2)))
            bars = closed_bars(m15, mode, now) if len(m15) else m15
            sc = signal_config(store, cfg, sym, mode_name)
            newest = f"{m15['bar_ts'].max():%Y-%m-%d %H:%M}" if len(m15) else "none"
            print(f"{sym + ' ' + mode_name:14} {mode.timeframe:9} {len(m15):>9} {newest:>18} {len(bars):>11}  "
                  f"{sc.source}")
            if len(m15) == 0:
                ok = False
            elif len(bars) < 250:
                print(f"{'':14} only {len(bars)} closed bars — indicators need ~{cfg.defaults.params['ema_slow'] * 3}+ "
                      "to settle; backfill more history in Feed")
        try:
            ev = store.news_events(now - timedelta(days=7), now + timedelta(days=7))
            print(f"\nNews: {len(ev)} events in ±7 days ({int((ev['impact'] == 'HIGH').sum()) if len(ev) else 0} high-impact)")
        except Exception as exc:  # noqa: BLE001
            print(f"\nNews: FAILED {exc}")
    print("\nAll good." if ok else "\nSome pairs have no data — run Feed's backfill.")
    return 0 if ok else 1


def print_results(results) -> None:
    for r in results:
        head = f"{r.symbol} {r.mode}"
        if r.status != "evaluated":
            print(f"  {head:14} [{r.status}] {r.notes}")
        else:
            print(f"  {head:14} {r.tier:9} {r.direction or '':4} score {r.score:.2f}"
                  f"{'  NEW SIGNAL x' + str(r.new_signals) if r.new_signals else ''}")
            print(f"  {'':14} {r.notes}")
        if r.warning:
            print(f"  {'':14} WARNING: {r.warning}")


def run_cycle(cfg: CoreConfig, store, now: datetime):
    results = []
    for sym, mode in cfg.pairs():
        try:
            results.append(evaluate_pair(store, cfg, sym, mode, now))
        except Exception:  # noqa: BLE001 — one bad pair mustn't stop the rest
            log.exception("%s %s: evaluation failed", sym, mode)
    return results


def cmd_once(cfg: CoreConfig, csv_dir) -> int:
    with open_store(cfg, csv_dir) as store:
        results = run_cycle(cfg, store, utc_now())
    print_results(results)
    return 0


def cmd_run(cfg: CoreConfig, csv_dir) -> int:
    setup_logging(cfg)
    log.info("Core loop started for %s", ", ".join(f"{s} {m}" for s, m in cfg.pairs()))
    try:
        while True:
            try:
                with open_store(cfg, csv_dir) as store:
                    for r in run_cycle(cfg, store, utc_now()):
                        if r.status == "evaluated":
                            log.info("%s %s: %s %s score %.2f%s", r.symbol, r.mode, r.tier, r.direction or "",
                                     r.score, f" — {r.new_signals} new signal(s)" if r.new_signals else "")
                        elif r.status not in ("already evaluated",):
                            log.info("%s %s: %s — %s", r.symbol, r.mode, r.status, r.notes)
                        if r.warning:
                            log.warning("%s %s: %s", r.symbol, r.mode, r.warning)
            except Exception:  # noqa: BLE001 — DB hiccup: retry next bar
                log.exception("Core cycle failed; retrying after the next bar")
            now = time.time()
            wait = (now // 900 + 1) * 900 + cfg.seconds_after_bar_close - now
            time.sleep(max(wait, 1))
    except KeyboardInterrupt:
        log.info("Stopped by user")
    return 0


def cmd_status(cfg: CoreConfig, csv_dir) -> int:
    with open_store(cfg, csv_dir) as store:
        st = store.list_states()
    if st.empty:
        print("No state yet — run `python main.py once`.")
        return 0
    for r in st.itertuples(index=False):
        print(f"{r.symbol} {r.mode}: {r.tier} {r.direction or ''} score {r.score}  (updated {r.updated_at} UTC)")
        print(f"    {r.notes}")
    return 0


def cmd_replay(cfg: CoreConfig, args) -> int:
    sym, mode = args.symbol.upper(), args.mode
    with open_store(cfg, args.csv_dir) as store:
        df, sc = replay(store, cfg, sym, mode)
    if df.empty:
        print(f"No transitions — is there data for {sym}?")
        return 1
    out = Path(args.out) if args.out else cfg.output_dir / f"replay_{sym}_{mode}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    entries = df[df["is_entry"]]
    print(f"Replayed {sym} {mode} with {sc.source}: {len(df)} tier changes, "
          f"{int((entries['tier'] == 'CONFIRMED').sum())} Tier 3 entries, "
          f"{int((entries['tier'] == 'FORMING').sum())} Tier 2 entries")
    print(f"First {df['signal_ts'].min()}, last {df['signal_ts'].max()} UTC")
    print(f"Saved {out}\n\nBacktest it (from quantora_backtest):\n"
          f"  python main.py run --symbol {sym} --mode {mode} --strategy core_signals --signals \"{out.resolve()}\"")
    return 0


def cmd_diagnose(cfg: CoreConfig, args) -> int:
    sym, mode_name = args.symbol.upper(), args.mode
    mode = cfg.modes[mode_name]
    with open_store(cfg, args.csv_dir) as store:
        sc = signal_config(store, cfg, sym, mode_name)
        m15 = store.load_m15(sym)
        if m15.empty:
            print(f"No data for {sym}")
            return 1
        evals = evaluate_bars(store, cfg, sym, mode, sc, closed_bars(m15, mode, utc_now()))
    res = collinearity(evals, sc.params, cfg.asset(sym) == "CRYPTO")
    if "note" in res:
        print(res["note"])
        return 1
    print(f"{sym} {mode_name} ({sc.source}), {res['bars']} bars")
    print(f"\n{len(res['dimensions'])} indicator votes measure ~{res['effective_independent']} independent things.\n")
    corr = pd.DataFrame(res["correlation"])
    print("Correlation of votes:\n" + corr.to_string())
    print("\nVIF (above 5 = redundant): " + ", ".join(f"{k} {v}" for k, v in res["vif"].items()))
    if res["flagged_pairs"] or res["flagged_vif"]:
        print("\nFLAGGED: " + "; ".join([f"{p['a']}~{p['b']} rho {p['rho']}" for p in res["flagged_pairs"]] +
                                       [f"VIF {k}" for k in res["flagged_vif"]]) +
              " — consider dropping or merging these (spec Section 5)")
    else:
        print("\nNo redundant pairs flagged.")
    if args.json:
        Path(args.json).write_text(json.dumps(res, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Quantora Core — three-tier confluence engine")
    p.add_argument("--settings", help="use a different settings file")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, help_ in [("check", "DB, data and config per pair"), ("once", "evaluate all pairs once"),
                        ("run", "evaluate after every bar close, forever"), ("status", "current tiers")]:
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--csv-dir", help="use Feed --dry-run CSVs + local state files instead of Oracle")
    for name, help_ in [("replay", "history -> signals CSV for the backtester"),
                        ("diagnose", "collinearity / VIF check of the indicator votes")]:
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--symbol", required=True)
        sp.add_argument("--mode", default="SWING", choices=["SCALP", "SWING", "LONG"])
        sp.add_argument("--csv-dir", help="use Feed --dry-run CSVs instead of Oracle")
        sp.add_argument("--out" if name == "replay" else "--json", help="output file")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(Path(args.settings) if args.settings else None)
    if args.cmd == "check":
        return cmd_check(cfg, args.csv_dir)
    if args.cmd == "once":
        return cmd_once(cfg, args.csv_dir)
    if args.cmd == "run":
        return cmd_run(cfg, args.csv_dir)
    if args.cmd == "status":
        return cmd_status(cfg, args.csv_dir)
    if args.cmd == "replay":
        return cmd_replay(cfg, args)
    return cmd_diagnose(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
