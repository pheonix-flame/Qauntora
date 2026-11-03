"""Interface every economic-calendar provider implements."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime

import pandas as pd
import requests

from models import NewsEvent

USER_AGENT = "quantora-feed/0.1"
IMPACTS = {"high": "HIGH", "medium": "MEDIUM", "low": "LOW"}


def to_naive_utc(value: str, assume_utc: bool = False) -> datetime:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        if not assume_utc:
            raise ValueError(f"timestamp without zone: {value}")
        return ts.to_pydatetime()
    return ts.tz_convert("UTC").tz_localize(None).to_pydatetime()


class NewsProvider(ABC):
    name: str = ""

    def __init__(self, session: requests.Session | None = None) -> None:
        self.http = session or requests.Session()
        self.http.headers["User-Agent"] = USER_AGENT

    @abstractmethod
    def fetch(self, start: date | None = None, end: date | None = None) -> list[NewsEvent]:
        """Events in [start, end]; providers that only expose a fixed window may ignore the range."""

    def _get_json(self, url: str, params: dict | None = None):
        resp = self.http.get(url, params=params or {}, timeout=30)
        resp.raise_for_status()
        return resp.json()


class NullProvider(NewsProvider):
    name = "none"

    def fetch(self, start: date | None = None, end: date | None = None) -> list[NewsEvent]:
        return []
