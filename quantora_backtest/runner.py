"""One backtest: symbol x mode x strategy -> walk-forward, metrics, baselines, gate, report."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import baselines
import gate
import metrics
import walk_forward
from config import BacktestConfig, CostModel, ModeProfile
from strategies import Strategy
from timeframes import rollup


@dataclass
class BacktestReport:
    symbol: str
    mode: ModeProfile
    strategy: Strategy
    cost: CostModel
    wf: walk_forward.WalkForwardResult
    oos_metrics: dict
    in_sample_expectancy_r: float | None
    random: dict
    always_long: dict
    buy_and_hold: dict
    rules: list[gate.Rule]
    data_first: pd.Timestamp
    data_last: pd.Timestamp
    risk_pct: float = 1.0
    tiers: dict | None = None

    @property
    def passed(self) -> bool:
        return gate.verdict(self.rules)

    def to_json(self) -> dict:
        """Shaped like qtx_config_versions.backtest_metrics_json, for Calibrate later."""
        return {
            "symbol": self.symbol, "mode": self.mode.name, "strategy": self.strategy.name,
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "data_window_utc": [str(self.data_first), str(self.data_last)],
            "out_of_sample_window_utc": [str(self.wf.oos_start), str(self.wf.oos_end)],
            "mode_profile": asdict(self.mode), "costs": asdict(self.cost),
            "configs_evaluated_count": self.wf.configs_evaluated,
            "risk_pct_per_trade": self.risk_pct,
            "out_of_sample": self.oos_metrics,
            "in_sample_avg_expectancy_r": self.in_sample_expectancy_r,
            "tier3_vs_tier2": self.tiers,
            "baselines": {"random_entry": self.random, "always_long": self.always_long,
                          "buy_and_hold": self.buy_and_hold},
            "folds": [{"fold": f.fold.index, "train": [str(f.fold.train_start), str(f.fold.train_end)],
                       "test": [str(f.fold.test_start), str(f.fold.test_end)], "params": f.params,
                       "train_trades": f.train_trades, "train_expectancy_r": _r(f.train_expectancy_r),
                       "test_trades": f.test_trades, "test_expectancy_r": _r(f.test_expectancy_r),
                       "note": f.note} for f in self.wf.folds],
            "gate": {"passed": self.passed,
                     "rules": [{"rule": r.name, "passed": r.passed, "detail": r.detail} for r in self.rules]},
            "collinearity_vif": "not applicable to this strategy — Calibrate adds it with Core's indicators",
        }


def _r(x):
    return None if x is None else round(float(x), 4)


def run_backtest(cfg: BacktestConfig, m15: pd.DataFrame, symbol: str, mode_name: str,
                 strategy: Strategy, runs: int | None = None, seed: int | None = None) -> BacktestReport:
    mode = cfg.modes[mode_name]
    cost = cfg.cost_for(symbol)
    if m15.empty:
        raise LookupError(f"No M15 bars for {symbol} — run the Feed backfill first")
    bars = rollup(m15, mode.timeframe)
    wf = walk_forward.run(strategy, m15, bars, mode, cost)
    oos = metrics.compute(wf.oos_trades, cfg.gate.risk_pct, wf.oos_days)
    tested = [f.train_expectancy_r for f in wf.folds if f.params is not None and f.train_expectancy_r is not None]
    tiers = None
    if hasattr(strategy, "tier_variant"):
        wf2 = walk_forward.run(strategy.tier_variant(), m15, bars, mode, cost)
        tiers = {"tier3_trades": int(len(wf.oos_trades)),
                 "tier3_expectancy_r": _r(wf.oos_trades["r"].mean()) if len(wf.oos_trades) else None,
                 "tier2_trades": int(len(wf2.oos_trades)),
                 "tier2_expectancy_r": _r(wf2.oos_trades["r"].mean()) if len(wf2.oos_trades) else None}
    rand = baselines.random_entry(m15, bars, mode, cost, wf, runs or cfg.random_runs,
                                  cfg.seed if seed is None else seed)
    return BacktestReport(
        symbol=symbol, mode=mode, strategy=strategy, cost=cost, wf=wf, oos_metrics=oos,
        in_sample_expectancy_r=_r(np.mean(tested)) if tested else None,
        random=rand, always_long=baselines.always_long(m15, mode, cost, wf),
        buy_and_hold=baselines.buy_and_hold(m15, wf),
        rules=gate.evaluate(oos, rand, cfg.gate, tiers), tiers=tiers,
        data_first=m15["bar_ts"].iloc[0], data_last=m15["bar_ts"].iloc[-1], risk_pct=cfg.gate.risk_pct)


def save(report: BacktestReport, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    base = out_dir / f"{report.symbol}_{report.mode.name}_{report.strategy.name}_{stamp}"
    json_path, csv_path = base.with_suffix(".json"), base.with_name(base.name + "_trades.csv")
    json_path.write_text(json.dumps(report.to_json(), indent=2, default=str), encoding="utf-8")
    report.wf.oos_trades.to_csv(csv_path, index=False)
    return json_path, csv_path
