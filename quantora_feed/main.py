"""Quantora Feed — command-line entry point.

    python main.py check                 # verify broker login, symbols, broker clock, DB, news
    python main.py init-db               # create the qtx_ tables in Oracle (once)
    python main.py backfill --days 365   # pull history for every configured symbol
    python main.py once                  # one incremental cycle (for Task Scheduler / cron)
    python main.py run                   # loop forever, waking just after each M15 close
    python main.py verify --days 7       # prove the stored data is right (see verification/)

Add --dry-run to backfill/once/run to write CSVs into preview_output/ instead of the DB.
Add --symbols V75,EURUSD to limit a run to some symbols.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler

from config import FeedConfig, load_config
from models import BAR_MINUTES
from news_sources import build_news_provider
from pipeline import SourcePool, seconds_until_next_bar, sync_all, sync_news, utc_now_naive
from storage import open_store

log = logging.getLogger("quantora.feed")


def setup_logging(cfg: FeedConfig, verbose: bool) -> None:
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    fmt.converter = time.gmtime  # log in UTC, like everything else
    root = logging.getLogger("quantora")
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    filelog = RotatingFileHandler(cfg.log_dir / "feed.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    filelog.setFormatter(fmt)
    root.handlers[:] = [console, filelog]


# -- commands -----------------------------------------------------------------
def cmd_check(cfg: FeedConfig) -> int:
    ok = True
    print("\n== Price sources ==")
    pool = SourcePool(cfg)
    try:
        for name, src in pool.connect_all().items():
            print(f"[{name}] broker clock: {src.describe_clock()}")
        print(f"{'key':8} {'src':5} {'broker symbol':24} {'min lot':>8} {'step':>7} {'contract':>9} "
              f"{'tick val':>9} {'spread pts':>10} {'spread price':>12}")
        cost_lines = []
        for spec in cfg.symbols:
            src = pool.source_for(spec)
            info = src.symbol_specs(spec)
            if info is None:
                ok = False
                hint = ", ".join(src.suggest_names(spec.broker_symbol.split()[0])) or "none"
                print(f"{spec.key:8} {spec.source:5} {spec.broker_symbol:24} NOT FOUND — similar: {hint}")
                continue
            spread = round(info["spread_points"] * info["point"], info["digits"])
            print(f"{spec.key:8} {spec.source:5} {spec.broker_symbol:24} {info['volume_min']:>8} "
                  f"{info['volume_step']:>7} {info['contract_size']:>9} {info['tick_value']:>9.4g} "
                  f"{info['spread_points']:>10} {spread:>12}")
            slippage = round(spread / 2, info["digits"] + 1)
            cost_lines.append(f"{spec.key} = {{ spread = {spread}, slippage = {slippage} }}")
        if cost_lines:
            print("\nTrading costs for the backtester — paste under [costs] in quantora_backtest/settings.toml.")
            print("(Live spread right now; run this during normal market hours. Slippage assumed = half the spread.)")
            print("\n".join(cost_lines))
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"FAILED: {exc}")
    finally:
        pool.close()

    print("\n== Database ==")
    try:
        with open_store(cfg) as store:
            print(store.describe())
            print("Schema present." if store.schema_exists()
                  else "Schema missing — run: python main.py init-db")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"FAILED: {exc}")

    print(f"\n== News ({cfg.news.provider}) ==")
    try:
        events = build_news_provider(cfg.news).fetch()
        high = sum(e.impact == "HIGH" for e in events)
        print("disabled" if cfg.news.provider == "none" else f"{len(events)} events ({high} high-impact)")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"FAILED: {exc}")

    print("\nAll good." if ok else "\nSome checks failed — see above.")
    return 0 if ok else 1


def cmd_init_db(cfg: FeedConfig) -> int:
    with open_store(cfg) as store:
        created = store.init_schema(cfg.schema_file)
    log.info("%s (%s)", "Schema created" if created else "Schema already exists — nothing done", cfg.schema_file)
    return 0


def cmd_sync(cfg: FeedConfig, dry_run: bool, backfill_days: int | None = None) -> int:
    """`backfill` (with backfill_days) or `once` (incremental)."""
    if dry_run:
        log.info("DRY RUN — writing CSVs to %s, database untouched", cfg.preview_dir)
    force_from = utc_now_naive() - timedelta(days=backfill_days) if backfill_days else None
    pool = SourcePool(cfg)
    with open_store(cfg, dry_run) as store:
        try:
            total, failed = sync_all(pool, store, cfg, force_from)
            sync_news(build_news_provider(cfg.news), store)
        finally:
            pool.close()
    log.info("Done: %d bars inserted%s", total, f"; failed: {failed}" if failed else "")
    return 1 if failed else 0


def cmd_run(cfg: FeedConfig, dry_run: bool) -> int:
    if dry_run:
        log.info("DRY RUN — writing CSVs to %s, database untouched", cfg.preview_dir)
    news = build_news_provider(cfg.news)
    pool = SourcePool(cfg)
    last_news = 0.0
    log.info("Feed loop started for %d symbols: %s", len(cfg.symbols), ", ".join(s.key for s in cfg.symbols))
    with open_store(cfg, dry_run) as store:
        try:
            while True:
                started = time.time()
                try:
                    _, failed = sync_all(pool, store, cfg)
                    if failed and len(failed) == len(cfg.symbols):
                        log.error("Every symbol failed — reconnecting sources next cycle")
                        pool.reset()
                except Exception:  # noqa: BLE001 — stay alive; Notify's health alerts come later
                    log.exception("Feed cycle failed; will retry next bar")
                    pool.reset()
                if time.time() - last_news >= cfg.news.refresh_hours * 3600:
                    sync_news(news, store)
                    last_news = time.time()
                log.info("Cycle finished in %.1fs", time.time() - started)
                wait = seconds_until_next_bar(datetime.now(timezone.utc), BAR_MINUTES[cfg.base_timeframe],
                                              cfg.bar_close_delay_s)
                time.sleep(max(wait, 1))
        except KeyboardInterrupt:
            log.info("Stopped by user")
        finally:
            pool.close()
    return 0


def cmd_verify(cfg: FeedConfig, days: int, dry_run: bool, use_broker: bool, use_reference: bool) -> int:
    from verification import run_verification
    from verification.base import Status

    pool = SourcePool(cfg) if use_broker else None
    with open_store(cfg, dry_run) as store:
        try:
            report = run_verification(cfg, store, pool, use_reference=use_reference, days=days)
        finally:
            if pool is not None:
                pool.close()

    marks = {Status.PASS: "ok  ", Status.WARN: "WARN", Status.FAIL: "FAIL", Status.SKIP: "skip"}
    print(f"\nVerification of the last {days} days ({'preview CSVs' if dry_run else 'database'})\n")
    current = None
    for r in report.results:
        if r.symbol != current:
            current = r.symbol
            print(f"── {r.symbol}")
        print(f"   [{marks[r.status]}] {r.check:24} {r.detail}")

    if report.samples:
        print("\nSpot-check by eye: open the MT5 chart on M15 and hover these candles. MT5 charts show")
        print("the broker clock, so match the 'broker_clock' column; Quantora stores the 'utc' column.\n")
        for key, sample in report.samples.items():
            print(f"{key}\n{sample.to_string(index=False)}\n")

    counts = {s: sum(r.status == s for r in report.results) for s in Status}
    print(f"Summary: {counts[Status.PASS]} passed, {counts[Status.WARN]} warnings, "
          f"{counts[Status.FAIL]} failed, {counts[Status.SKIP]} skipped")
    if report.worst == Status.FAIL:
        print("Result: FAIL — don't build on this data until the failures above are fixed.")
        return 1
    print("Result: data looks right." if report.worst == Status.PASS
          else "Result: no errors; read the warnings above.")
    return 0


# -- argument parsing -----------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Quantora Feed — market data engine")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="verify broker, symbols, DB and news setup")
    sub.add_parser("init-db", help="create qtx_ tables in Oracle")
    for name, help_ in [("backfill", "pull N days of history"), ("once", "one incremental cycle"),
                        ("run", "loop forever on every M15 close")]:
        p = sub.add_parser(name, help=help_)
        p.add_argument("--dry-run", action="store_true", help="write CSVs to preview_output/, not the DB")
        p.add_argument("--symbols", help="comma-separated keys, e.g. V75,EURUSD")
        if name == "backfill":
            p.add_argument("--days", type=int, default=None, help="days of history (default BACKFILL_DAYS)")
    v = sub.add_parser("verify", help="check stored data against MT5 and an independent source")
    v.add_argument("--days", type=int, default=7, help="how many days back to verify (default 7)")
    v.add_argument("--symbols", help="comma-separated keys, e.g. V75,EURUSD")
    v.add_argument("--dry-run", action="store_true", help="verify the preview_output/ CSVs instead of the DB")
    v.add_argument("--no-broker", action="store_true", help="skip the MT5 cross-checks")
    v.add_argument("--no-reference", action="store_true", help="skip the Coinbase clock check")
    return parser


def main(argv: list[str] | None = None) -> int:
    # Windows writes piped/redirected output in cp1252, which can't encode → × — or emoji: force UTF-8.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = load_config()
    if getattr(args, "symbols", None):
        wanted = {s.strip().upper() for s in args.symbols.split(",")}
        cfg = replace(cfg, symbols=[s for s in cfg.symbols if s.key in wanted])
        if not cfg.symbols:
            parser.error(f"no configured symbols match {sorted(wanted)}")
    setup_logging(cfg, args.verbose)

    if args.cmd == "check":
        return cmd_check(cfg)
    if args.cmd == "init-db":
        return cmd_init_db(cfg)
    if args.cmd == "backfill":
        return cmd_sync(cfg, args.dry_run, backfill_days=args.days or cfg.backfill_days)
    if args.cmd == "once":
        return cmd_sync(cfg, args.dry_run)
    if args.cmd == "verify":
        return cmd_verify(cfg, args.days, args.dry_run, not args.no_broker, not args.no_reference)
    return cmd_run(cfg, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
