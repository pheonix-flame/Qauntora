"""Financial Modeling Prep economic calendar — needs FMP_API_KEY.

Supports arbitrary date ranges. FMP has moved this endpoint between free and
paid tiers over time; `python main.py check` will show an error if your plan
doesn't include it.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from models import NewsEvent
from news_sources.base import IMPACTS, NewsProvider, to_naive_utc
from resilience import with_retry

FMP_URL = "https://financialmodelingprep.com/stable/economic-calendar"


def parse(payload: list[dict]) -> list[NewsEvent]:
    events = []
    for row in payload:
        impact = IMPACTS.get(str(row.get("impact", "")).lower())
        if impact is None:
            continue
        try:
            ts = to_naive_utc(row["date"], assume_utc=True)  # FMP dates are UTC
        except (KeyError, ValueError):
            continue
        currency = row.get("currency") or row.get("country") or ""
        events.append(NewsEvent(ts, str(currency)[:5].upper(), impact,
                                str(row.get("event", "")).strip()[:200]))
    return events


class FMPProvider(NewsProvider):
    name = "fmp"

    def __init__(self, api_key: str, session=None) -> None:
        if not api_key:
            raise ValueError("NEWS_PROVIDER=fmp but FMP_API_KEY is empty")
        super().__init__(session)
        self.api_key = api_key

    def fetch(self, start: date | None = None, end: date | None = None) -> list[NewsEvent]:
        today = datetime.now(timezone.utc).date()
        params = {
            "from": (start or today - timedelta(days=7)).isoformat(),
            "to": (end or today + timedelta(days=14)).isoformat(),
            "apikey": self.api_key,
        }
        data = with_retry(lambda: self._get_json(FMP_URL, params), "FMP calendar fetch", attempts=3)
        if isinstance(data, dict):  # FMP returns {"Error Message": ...} on plan/key problems
            raise RuntimeError(f"FMP error: {data}")
        return parse(data)
