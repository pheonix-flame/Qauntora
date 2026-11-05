"""Strategy registry. Add a strategy = add a module + one line here.

Core's confluence engine (build step 3) will be backtested through this same
harness, so it's judged by exactly the same costs, barriers and baselines.
"""
from __future__ import annotations

from strategies.base import Strategy
from strategies.core_signals import CoreSignals
from strategies.ema_cross import EmaCross

REGISTRY: dict[str, type[Strategy]] = {
    EmaCross.name: EmaCross,
    CoreSignals.name: CoreSignals,
}


def get_strategy(name: str, **kwargs) -> Strategy:
    """kwargs go to strategies that need them (core_signals: path, symbol, mode)."""
    try:
        cls = REGISTRY[name]
    except KeyError:
        raise ValueError(f"Unknown strategy '{name}'. Available: {sorted(REGISTRY)}") from None
    return cls(**kwargs) if kwargs else cls()


__all__ = ["REGISTRY", "Strategy", "get_strategy"]
