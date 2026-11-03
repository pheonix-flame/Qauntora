"""Free weekly economic calendar (ForexFactory export) — no API key.

Covers the current week only, so Feed refreshes it every few hours and keeps
the history itself in qtx_news_events. It's a free community feed: don't poll
it more than a few times an hour.
"""
from __future__ import annotations

import logging
from datetime import date

from models import NewsEvent
from news_sources.base import IMPACTS, NewsProvider, to_naive_utc
from resilience import with_retry

log = logging.getLogger("quantora.feed")

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


def parse(payload: list[dict]) -> list[NewsEvent]:
    events = []
    for row in payload:
        impact = IMPACTS.get(str(row.get("impact", "")).lower())
        if impact is None:  # Holiday / Non-Economic
            continue
        try:
            ts = to_naive_utc(row["date"])
        except (KeyError, ValueError) as exc:
            log.debug("Skipping calendar row with bad date %r: %s", row, exc)
            continue
        events.append(NewsEvent(ts, str(row.get("country", ""))[:5].upper(), impact,
                                str(row.get("title", "")).strip()[:200]))
    return events


class ForexFactoryProvider(NewsProvider):
    name = "forexfactory"

    def fetch(self, start: date | None = None, end: date | None = None) -> list[NewsEvent]:
        data = with_retry(lambda: self._get_json(FF_URL), "ForexFactory calendar fetch", attempts=3)
        return parse(data)
