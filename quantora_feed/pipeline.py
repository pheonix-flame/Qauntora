"""Quantora Feed — orchestration.

Wires price sources, news provider and store together. Knows nothing about
MT5, ccxt, SQL Server or CSVs specifically — only the interfaces in
price_sources/base.py, news_sources/base.py and storage/base.py.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Callable

from config import FeedConfig, SymbolSpec
from models import BAR_MINUTES
from news_sources import NewsProvider
from price_sources import PriceSource, build_price_source
from resilience import with_retry
from storage import Store

log = logging.getLogger("quantora.feed")


class SourcePool:
    """One connected PriceSource per source name used by the configured symbols."""

    def __init__(self, cfg: FeedConfig, factory=build_price_source) -> None:
        self.cfg = cfg
        self._factory = factory
        self._by_name: dict[str, list[SymbolSpec]] = defaultdict(list)
        for spec in cfg.symbols:
            self._by_name[spec.source].append(spec)
        self._sources: dict[str, PriceSource] = {}

    def source_for(self, spec: SymbolSpec) -> PriceSource:
        src = self._sources.get(spec.source)
        if src is None or not src.connected:
            src = self._factory(spec.source, self.cfg)
            with_retry(lambda: src.connect(self._by_name[spec.source]), f"{spec.source} connect")
            self._sources[spec.source] = src
        return src

    def connect_all(self) -> dict[str, PriceSource]:
        for specs in self._by_name.values():
            self.source_for(specs[0])
        return dict(self._sources)

    def reset(self, name: str | None = None) -> None:
        for key in [name] if name else list(self._sources):
            src = self._sources.pop(key, None)
            if src is not None:
                try:
                    src.shutdown()
                except Exception:  # noqa: BLE001
                    log.debug("shutdown of %s failed", key, exc_info=True)

    def close(self) -> None:
        self.reset()


def utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def sync_symbol(pool: SourcePool, store: Store, spec: SymbolSpec, timeframe: str,
                backfill_days: int, force_from: datetime | None = None,
                now: Callable[[], datetime] = utc_now_naive) -> int:
    """Fetch everything after the newest stored bar (or `backfill_days` back) and store it."""
    if force_from is not None:
        start = force_from
    else:
        latest = store.latest_bar_ts(spec.key, timeframe)
        start = (latest + timedelta(minutes=BAR_MINUTES[timeframe]) if latest is not None
                 else now() - timedelta(days=backfill_days))
    df = pool.source_for(spec).fetch_bars(spec, start, timeframe)
    added = store.insert_ohlcv(df)
    span = f"{df['bar_ts'].min()} → {df['bar_ts'].max()}" if len(df) else "no new closed bars"
    log.info("%-7s %s: fetched %d, inserted %d (%s)", spec.key, timeframe, len(df), added, span)
    return added


def sync_all(pool: SourcePool, store: Store, cfg: FeedConfig,
             force_from: datetime | None = None,
             now: Callable[[], datetime] = utc_now_naive) -> tuple[int, list[str]]:
    """Sync every symbol; one failing symbol never stops the rest."""
    total, failed = 0, []
    for spec in cfg.symbols:
        try:
            total += sync_symbol(pool, store, spec, cfg.base_timeframe, cfg.backfill_days, force_from, now)
        except LookupError as exc:
            log.warning("%s: %s — skipped (run `python main.py check`)", spec.key, exc)
            failed.append(spec.key)
        except Exception:  # noqa: BLE001
            log.exception("%s: sync failed", spec.key)
            failed.append(spec.key)
    return total, failed


def sync_news(news: NewsProvider, store: Store) -> int:
    try:
        events = news.fetch()
        added = store.insert_news(events)
        if news.name != "none":
            log.info("News (%s): fetched %d events, inserted %d new", news.name, len(events), added)
        return added
    except Exception:  # noqa: BLE001
        log.exception("News fetch failed (Core's blackout filter will be working from stale data)")
        return 0


def seconds_until_next_bar(now: datetime, bar_minutes: int, delay_s: int) -> float:
    """Seconds until `delay_s` after the next bar boundary (boundaries are UTC-aligned)."""
    epoch = now.timestamp()
    period = bar_minutes * 60
    next_close = (epoch // period + 1) * period
    return next_close + delay_s - epoch
