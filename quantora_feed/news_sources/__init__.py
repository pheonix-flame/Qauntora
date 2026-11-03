"""News-provider registry, selected by NEWS_PROVIDER in .env.

Finnhub's economic calendar is premium-only, so it isn't wired in; adding it
(or any other provider) is one new module plus one line below.
"""
from __future__ import annotations

from config import NewsConfig
from news_sources.base import NewsProvider, NullProvider
from news_sources.fmp import FMPProvider
from news_sources.forexfactory import ForexFactoryProvider


def build_news_provider(cfg: NewsConfig) -> NewsProvider:
    builders = {
        "forexfactory": lambda: ForexFactoryProvider(),
        "fmp": lambda: FMPProvider(cfg.fmp_api_key),
        "none": lambda: NullProvider(),
    }
    try:
        return builders[cfg.provider]()
    except KeyError:
        raise ValueError(f"Unknown NEWS_PROVIDER '{cfg.provider}'. Available: {sorted(builders)}") from None


__all__ = ["NewsProvider", "build_news_provider"]
