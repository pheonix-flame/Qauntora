"""Promotion gate (spec Sections 8 & 17): may this config move on to demo trading?

Every rule must pass. One rule can't be checked yet and is reported as such:
Tier 3 vs Tier 2 (Section 4) needs Quantora Core's three-tier signals.
"""
from __future__ import annotations

from dataclasses import dataclass

from config import GateConfig


@dataclass(frozen=True)
class Rule:
    name: str
    passed: bool | None  # None = can't be evaluated yet
    detail: str


def evaluate(metrics: dict, random: dict, gate: GateConfig) -> list[Rule]:
    n = metrics.get("trades", 0)
    exp = metrics.get("expectancy_r")
    dd = metrics.get("max_drawdown_pct")
    pct = random.get("percentile")
    return [
        Rule("enough trades", n >= gate.min_trades,
             f"{n} out-of-sample trades (need {gate.min_trades}+)"),
        Rule("positive expectancy after costs", exp is not None and exp > gate.min_expectancy_r,
             f"{exp if exp is not None else 'n/a'} R per trade (need > {gate.min_expectancy_r})"),
        Rule("drawdown within limit", dd is not None and dd <= gate.max_drawdown_pct,
             f"{dd if dd is not None else 'n/a'}% max drawdown at {gate.risk_pct}% risk/trade "
             f"(limit {gate.max_drawdown_pct}%)"),
        Rule("beats random entry", pct is not None and pct >= gate.min_random_percentile,
             f"better than {pct if pct is not None else 'n/a'}% of random runs (need {gate.min_random_percentile}%+)"),
        Rule("Tier 3 beats Tier 2", None, "needs Quantora Core (build step 3) — not checked yet"),
    ]


def verdict(rules: list[Rule]) -> bool:
    return all(r.passed for r in rules if r.passed is not None)
