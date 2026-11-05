"""Promotion gate (spec Sections 8 & 17): may this config move on to demo trading?

Every rule must pass. "Tier 3 beats Tier 2" (Section 4) can only be checked for
Quantora Core's signals; for other strategies it's reported as not applicable.
"""
from __future__ import annotations

from dataclasses import dataclass

from config import GateConfig


@dataclass(frozen=True)
class Rule:
    name: str
    passed: bool | None  # None = can't be evaluated yet
    detail: str


def tier_rule(tiers: dict | None) -> Rule:
    """Section 4: waiting for Tier 3 must demonstrably beat acting at Tier 2."""
    if not tiers:
        return Rule("Tier 3 beats Tier 2", None,
                    "only checkable for Quantora Core signals (--strategy core_signals)")
    t3, t2, n2 = tiers.get("tier3_expectancy_r"), tiers.get("tier2_expectancy_r"), tiers.get("tier2_trades", 0)
    if t3 is None or t2 is None or not n2:
        return Rule("Tier 3 beats Tier 2", False, "no Tier 2 trades to compare against")
    return Rule("Tier 3 beats Tier 2", t3 > t2,
                f"Tier 3 {t3:+.3f} R vs Tier 2 {t2:+.3f} R per trade ({n2} Tier 2 trades)")


def evaluate(metrics: dict, random: dict, gate: GateConfig, tiers: dict | None = None) -> list[Rule]:
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
        tier_rule(tiers),
    ]


def verdict(rules: list[Rule]) -> bool:
    return all(r.passed for r in rules if r.passed is not None)
