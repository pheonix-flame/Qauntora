"""Quantora Validate — command-line entry point.

    python main.py check     # DB, how many signals wait for a label
    python main.py once      # label everything whose outcome is now known
    python main.py run       # do that every interval_minutes, forever
    python main.py report    # live outcomes per symbol/mode/tier, incl. Tier 3 vs Tier 2

Local mode (no DB):  --csv-dir ../quantora_feed/preview_output --core-dir ../quantora_core/core_output
Reads qtx_ohlcv + qtx_signals; writes only qtx_signal_outcomes (insert-only).
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from config import ValidateConfig, load_config
from storage import open_store
from validate import report, run_once

log = logging.getLogger("quantora.validate")
MIN_MEANINGFUL = 30  # spec Section 8: fewer resolved trades than this is not evidence


def print_summary(s) -> None:
    print(f"Labelled {s.labelled} signal(s)" + (": " + ", ".join(f"{k} {v}" for k, v in sorted(s.by_outcome.items()))
                                                if s.by_outcome else "."))
    for k, v in s.pending.items():
        print(f"  pending  {v:>4}  {k}")
    for k, v in s.skipped.items():
        print(f"  skipped  {v:>4}  {k}")


def cmd_check(cfg: ValidateConfig, a) -> int:
    try:
        with open_store(cfg, a.csv_dir, a.core_dir) as st:
            print(st.describe())
            sigs = st.unlabelled_signals()
            done = st.outcomes_report()
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED: {exc}")
        return 1
    print(f"Signals waiting for a label: {len(sigs)} "
          f"({int((sigs['tier'] == 'CONFIRMED').sum()) if len(sigs) else 0} Tier 3)")
    print(f"Outcomes already labelled:   {len(done)}")
    return 0


def cmd_once(cfg: ValidateConfig, a) -> int:
    with open_store(cfg, a.csv_dir, a.core_dir) as st:
        print_summary(run_once(st, cfg))
    return 0


def cmd_run(cfg: ValidateConfig, a) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    log.info("Validate loop: every %d min", cfg.interval_minutes)
    try:
        while True:
            try:
                with open_store(cfg, a.csv_dir, a.core_dir) as st:
                    s = run_once(st, cfg)
                log.info("labelled %d, pending %d", s.labelled, sum(s.pending.values()))
            except Exception:  # noqa: BLE001
                log.exception("Validate cycle failed; retrying next interval")
            time.sleep(cfg.interval_minutes * 60)
    except KeyboardInterrupt:
        return 0


def cmd_report(cfg: ValidateConfig, a) -> int:
    with open_store(cfg, a.csv_dir, a.core_dir) as st:
        rep = report(st.outcomes_report())
    if rep.empty:
        print("No labelled outcomes yet — Core needs to have produced signals that have since resolved.")
        return 0
    print(rep.to_string(index=False))
    for (sym, mode), g in rep.groupby(["symbol", "trade_mode"]):
        t = {r.tier: r for r in g.itertuples()}
        if "CONFIRMED" in t and "FORMING" in t:
            t3, t2 = t["CONFIRMED"], t["FORMING"]
            enough = t3.n >= MIN_MEANINGFUL and t2.n >= MIN_MEANINGFUL
            verdict = ("waiting for Tier 3 pays" if t3.expectancy_r > t2.expectancy_r else
                       "acting at Tier 2 did better — the patience premise isn't holding here")
            print(f"\n{sym} {mode}: Tier 3 {t3.expectancy_r:+.3f} R (n={t3.n}) vs Tier 2 {t2.expectancy_r:+.3f} R "
                  f"(n={t2.n}) — {verdict}" + ("" if enough else
                                              f" [not meaningful yet: need {MIN_MEANINGFUL}+ of each]"))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Quantora Validate — Triple Barrier outcome labelling")
    p.add_argument("--settings", help="use a different settings file")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("check", "once", "run", "report"):
        sp = sub.add_parser(name)
        sp.add_argument("--csv-dir", help="Feed --dry-run CSV folder (local mode)")
        sp.add_argument("--core-dir", help="Core's core_output folder (local mode)")
    a = p.parse_args(argv)
    cfg = load_config(Path(a.settings) if a.settings else None)
    return {"check": cmd_check, "once": cmd_once, "run": cmd_run, "report": cmd_report}[a.cmd](cfg, a)


if __name__ == "__main__":
    sys.exit(main())
