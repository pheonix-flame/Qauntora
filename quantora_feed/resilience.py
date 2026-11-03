"""Retry-with-backoff for every external connection (spec Section 12)."""
from __future__ import annotations

import logging
import time
from typing import Callable, TypeVar

T = TypeVar("T")
log = logging.getLogger("quantora.feed")


def with_retry(
    fn: Callable[[], T],
    what: str,
    attempts: int = 5,
    base_delay_s: float = 2.0,
    max_delay_s: float = 60.0,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call `fn`, retrying with exponential backoff. Re-raises the last error."""
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — we log and re-raise
            if attempt == attempts:
                log.error("%s failed after %d attempts: %s", what, attempts, exc)
                raise
            delay = min(base_delay_s * 2 ** (attempt - 1), max_delay_s)
            log.warning("%s failed (attempt %d/%d): %s — retrying in %.0fs",
                        what, attempt, attempts, exc, delay)
            sleep(delay)
    raise AssertionError("unreachable")
