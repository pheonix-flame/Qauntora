"""Strategy registry. Add a strategy = add a module + one line here.

Core's confluence engine (build step 3) will be backtested through this same
harness, so it's judged by exactly the same costs, barriers and baselines.
"""
from __future__ import annotations

from strategies.base import Strategy
from strategies.ema_cross import EmaCross

REGISTRY: dict[str, type[Strategy]] = {
    EmaCross.name: EmaCross,
}


def get_strategy(name: str) -> Strategy:
    try:
        return REGISTRY[name]()
    except KeyError:
        raise ValueError(f"Unknown strategy '{name}'. Available: {sorted(REGISTRY)}") from None


__all__ = ["REGISTRY", "Strategy", "get_strategy"]
