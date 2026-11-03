"""Price-source registry.

To add a source (e.g. ccxt for a spot exchange): create `price_sources/ccxt.py`
with a PriceSource subclass, add it to _REGISTRY, and set QTX_SOURCE_<KEY>=ccxt
for the symbols it should serve. Nothing else in Feed changes.
"""
from __future__ import annotations

from config import FeedConfig
from price_sources.base import PriceSource
from price_sources.mt5 import MT5Source

_REGISTRY: dict[str, type[PriceSource]] = {
    MT5Source.name: MT5Source,
}


def build_price_source(name: str, cfg: FeedConfig) -> PriceSource:
    try:
        return _REGISTRY[name].from_config(cfg)
    except KeyError:
        raise ValueError(f"Unknown price source '{name}'. Available: {sorted(_REGISTRY)}") from None


__all__ = ["PriceSource", "build_price_source"]
