"""Quantora Backtest — command-line entry point.

    python main.py check                                  # DB connection, history per symbol, costs set?
    python main.py strategies                             # list available strategies
    python main.py run --symbol V75 --mode SWING          # walk-forward backtest (default strategy: ema_cross)
    python main.py run --symbol V75 --mode SWING --csv-dir ../quantora_feed/preview_output
    python main.py demo                                   # no DB needed: runs on a synthetic random walk

Reads qtx_ohlcv only. Never writes to the database. Reports go to backtest_output/.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd

from config import BacktestConfig, CostModel, MissingCostError, load_config
from data_sources import open_source
from runner import BacktestReport, run_backtest, save
from strategies import REGISTRY, get_strategy
from synthetic import random_walk


def needed_days(cfg: BacktestConfig, mode: str) -> int:
    m = cfg.modes[mode]
    return m.train_days + m.embargo_days + m.test_days


def cmd_check(cfg: BacktestConfig, csv_dir: str | None) -> int:
    print("\n== Data ==")
    try:
        with open_source(cfg, csv_dir) as src:
            print(src.describe())
            inv = src.inventory()
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED: {exc}")
        return 1
    if inv.empty:
        print("No M15 bars stored yet — run `python main.py backfill` in quantora_feed first.")
        return 1
    modes = list(cfg.modes)
    print(f"\n{'symbol':8} {'bars':>8} {'from':>17} {'to':>17} {'days':>6}  costs  " +
          "  ".join(f"{m:>5}" for m in modes))
    for row in inv.itertuples(index=False):
        days = (pd.Timestamp(row.last) - pd.Timestamp(row.first)).days
        has_cost = "yes" if row.symbol in cfg.costs else "NO "
        enough = "  ".join(f"{'ok' if days >= needed_days(cfg, m) else 'short':>5}" for m in modes)
        print(f"{row.symbol:8} {row.bars:>8} {pd.Timestamp(row.first):%Y-%m-%d %H:%M} "
              f"{pd.Timestamp(row.last):%Y-%m-%d %H:%M} {days:>6}  {has_cost}    {enough}")
    print("\nMode needs at least one train+embargo+test window: " +
          ", ".join(f"{m} {needed_days(cfg, m)} days" for m in modes) + " (more history = more folds = more trust).")
    missing = [s for s in inv["symbol"] if s not in cfg.costs]
    if missing:
        print(f"Costs missing for {', '.join(missing)}: run `python main.py check` in quantora_feed and paste "
              f"its lines under [costs] in {cfg.settings_file}")
    return 0


def print_report(rep: BacktestReport) -> None:
    m, wf = rep.oos_metrics, rep.wf
    print(f"\n=== {rep.symbol} · {rep.mode.name} ({rep.mode.timeframe}) · strategy {rep.strategy.name} ===")
    print(f"Data {rep.data_first:%Y-%m-%d} → {rep.data_last:%Y-%m-%d} UTC · "
          f"costs: spread {rep.cost.spread}, slippage {rep.cost.slippage}")
    print(f"Barriers: SL {rep.mode.sl_atr}×ATR, TP {rep.mode.tp_atr}×ATR, max hold {rep.mode.max_hold_minutes} min\n")
    print("Walk-forward folds (parameters picked on TRAIN, results from TEST only):")
    for f in wf.folds:
        if f.params is None:
            print(f"  fold {f.fold.index}: {f.note}")
            continue
        print(f"  fold {f.fold.index}: test {f.fold.test_start:%Y-%m-%d}→{f.fold.test_end:%Y-%m-%d}  "
              f"params {f.params}  train {f.train_trades} trades {f.train_expectancy_r:+.3f}R  →  "
              f"test {f.test_trades} trades " +
              (f"{f.test_expectancy_r:+.3f}R" if f.test_expectancy_r is not None else "—"))
    print(f"  configurations evaluated: {wf.configs_evaluated}  (overfitting guard — more tries, more luck)")

    if not m["trades"]:
        print("\nNo out-of-sample trades — not enough history for this mode yet (see `python main.py check`).")
    else:
        print(f"\nOut-of-sample: {m['trades']} trades ({m['wins']} win / {m['losses']} loss / "
              f"{m['timeouts']} timeout), win rate {m['win_rate']:.0%}")
        print(f"  expectancy {m['expectancy_r']:+.3f} R/trade   profit factor {m['profit_factor']}   "
              f"avg win {m['avg_win_r']} R   avg loss {m['avg_loss_r']} R")
        print(f"  at {rep.risk_pct}% risk/trade: return {m['total_return_pct']}%   "
              f"max drawdown {m['max_drawdown_pct']}%   Sharpe {m['sharpe']}   Sortino {m['sortino']}   "
              f"Calmar {m['calmar']}   Ulcer {m['ulcer_index']}")
        if rep.in_sample_expectancy_r is not None:
            print(f"  in-sample (train) average was {rep.in_sample_expectancy_r:+.3f} R — a big drop from train "
                  "to test is the classic sign of overfitting")

    r, al, bh = rep.random, rep.always_long, rep.buy_and_hold
    print("\nBaselines (same windows, barriers and costs):")
    if r.get("percentile") is not None:
        print(f"  random entry ×{r['runs']}: mean {r['mean_expectancy_r']:+.3f} R "
              f"(5–95%: {r['p5_expectancy_r']:+.3f} … {r['p95_expectancy_r']:+.3f})  → strategy beats "
              f"{r['percentile']}% of them")
    else:
        print("  random entry: n/a")
    if al.get("expectancy_r") is not None:
        print(f"  always long at the strategy's times: {al['expectancy_r']:+.3f} R over {al['trades']} trades")
    if bh.get("return_pct") is not None:
        print(f"  buy & hold over the test period: {bh['return_pct']}% (max drawdown {bh['max_drawdown_pct']}%)")

    print("\nPromotion gate (spec Section 8):")
    for rule in rep.rules:
        mark = "pass" if rule.passed else ("----" if rule.passed is None else "FAIL")
        print(f"  [{mark}] {rule.name:34} {rule.detail}")
    print("\nVERDICT: " + ("PASSES the gate — eligible for demo trading at a realistic balance."
                           if rep.passed else "does NOT pass — not evidence of an edge. Don't trade it."))


def cmd_run(cfg: BacktestConfig, args) -> int:
    symbol = args.symbol.upper()
    try:
        with open_source(cfg, args.csv_dir) as src:
            m15 = src.load_m15(symbol)
        rep = run_backtest(cfg, m15, symbol, args.mode, get_strategy(args.strategy), args.runs, args.seed)
    except MissingCostError as exc:
        print(f"\nCan't run: {exc}")
        return 2
    except LookupError as exc:
        print(f"\nCan't run: {exc}")
        return 2
    print_report(rep)
    json_path, csv_path = save(rep, cfg.output_dir)
    print(f"\nSaved: {json_path}\n       {csv_path}")
    return 0 if rep.passed else 1


def cmd_demo(cfg: BacktestConfig, args) -> int:
    print("DEMO on a synthetic random walk (2 years of M15). There is no edge in this data by construction —")
    print("a trustworthy harness must say so. If it ever passes here, the harness is broken, not brilliant.")
    m15 = random_walk(days=730, seed=args.seed or 0)
    cost = CostModel(spread=0.05, slippage=0.02)
    demo_cfg = replace(cfg, costs={**cfg.costs, "DEMO": cost})
    rep = run_backtest(demo_cfg, m15, "DEMO", args.mode, get_strategy(args.strategy), args.runs, args.seed)
    print_report(rep)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Quantora Backtest — purged walk-forward harness")
    p.add_argument("--settings", help="use a different settings file (default: settings.toml here)")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="DB connection, stored history per symbol, costs configured")
    c.add_argument("--csv-dir", help="read Feed --dry-run CSVs from this folder instead of Oracle")
    sub.add_parser("strategies", help="list strategies")
    for name in ("run", "demo"):
        r = sub.add_parser(name, help="walk-forward backtest" if name == "run" else "run on synthetic data, no DB")
        if name == "run":
            r.add_argument("--symbol", required=True, help="e.g. V75")
            r.add_argument("--csv-dir", help="read Feed --dry-run CSVs from this folder instead of Oracle")
        r.add_argument("--mode", default="SWING", choices=["SCALP", "SWING", "LONG"])
        r.add_argument("--strategy", default="ema_cross", choices=sorted(REGISTRY))
        r.add_argument("--runs", type=int, help="random-baseline runs (default from settings.toml)")
        r.add_argument("--seed", type=int, help="random seed (default from settings.toml)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(Path(args.settings) if args.settings else None)
    if args.cmd == "check":
        return cmd_check(cfg, args.csv_dir)
    if args.cmd == "strategies":
        for name, cls in REGISTRY.items():
            print(f"{name:12} {cls.description}\n{'':12} parameters tried: {cls.param_grid}")
        return 0
    if args.cmd == "run":
        return cmd_run(cfg, args)
    return cmd_demo(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
