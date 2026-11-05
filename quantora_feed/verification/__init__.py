"""`python main.py verify` — is the stored data actually right?

Three independent layers, each catching what the others can't:

0. clock       the broker-clock setting vs a live tick (offline; affects every symbol).
1. integrity   stored bars only: impossible prices, misaligned/future
               timestamps, gaps, frozen prices, spikes, staleness.
2. broker      vs MT5 itself: stored M15 == fresh MT5 pull (nothing lost or
               altered), and stored M15 rolls up exactly into MT5's own H1 bars.
3. reference   vs Coinbase (a different company): Deriv's BTC/ETH returns must
               line up with Coinbase's at zero time shift, proving the UTC
               conversion is right for every symbol (they share one broker clock).

Layers 2 and 3 are skipped (SKIP, not FAIL) when MT5 or the internet isn't available.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

import pandas as pd
import requests

from config import FeedConfig
from pipeline import SourcePool, utc_now_naive
from storage import Store
from verification import broker, integrity, reference
from verification.base import CheckResult, Status

log = logging.getLogger("quantora.feed")


@dataclass
class VerifyReport:
    results: list[CheckResult] = field(default_factory=list)
    samples: dict[str, pd.DataFrame] = field(default_factory=dict)

    @property
    def worst(self) -> Status:
        for s in (Status.FAIL, Status.WARN):
            if any(r.status == s for r in self.results):
                return s
        return Status.PASS


def run_verification(cfg: FeedConfig, store: Store, pool: SourcePool | None = None,
                     use_reference: bool = True, days: int = 7,
                     http: requests.Session | None = None,
                     fetch_reference: Callable = reference.fetch_coinbase,
                     now: Callable[[], datetime] = utc_now_naive) -> VerifyReport:
    report = VerifyReport()
    current = now()
    start = current - timedelta(days=days)

    # -- broker clock: the setting every symbol's timestamps depend on -------------
    if pool is not None:
        try:
            for name, src in pool.connect_all().items():
                ok, detail = src.verify_clock(pool.symbols_for(name))
                status = Status.SKIP if ok is None else (Status.PASS if ok else Status.FAIL)
                report.results.append(CheckResult(f"[{name} clock]", "UTC conversion", status, detail))
        except Exception as exc:  # noqa: BLE001
            report.results.append(CheckResult("[broker clock]", "UTC conversion", Status.SKIP,
                                              f"broker unavailable: {exc}"))
    for spec in cfg.symbols:
        stored = store.load_bars(spec.key, cfg.base_timeframe, start)
        stored["bar_ts"] = pd.to_datetime(stored["bar_ts"])
        report.results += integrity.check_integrity(stored, spec, current)
        if stored.empty:
            continue
        first, last = stored["bar_ts"].min().to_pydatetime(), stored["bar_ts"].max()

        # -- layer 2: broker ----------------------------------------------------
        if pool is None:
            report.results.append(CheckResult(spec.key, "matches MT5", Status.SKIP, "--no-broker"))
        else:
            try:
                src = pool.source_for(spec)
                fresh = src.fetch_bars(spec, first, cfg.base_timeframe)
                report.results.append(broker.round_trip(spec.key, stored, fresh))
                h1 = src.fetch_bars(spec, first, "H1")
                report.results.append(broker.h1_rebuild(spec.key, stored, h1))
                sample = stored.tail(3)[["bar_ts", "open_price", "high_price", "low_price", "close_price"]].copy()
                sample.insert(1, "broker_clock", sample["bar_ts"].map(src.utc_to_broker_clock))
                report.samples[spec.key] = sample.rename(columns={"bar_ts": "utc"})
            except Exception as exc:  # noqa: BLE001 — a broker problem shouldn't hide the other checks
                report.results.append(CheckResult(spec.key, "matches MT5", Status.SKIP, f"broker unavailable: {exc}"))

        # -- layer 3: independent reference ---------------------------------------
        product = reference.PRODUCTS.get(spec.key)
        if product and use_reference:
            try:
                window = stored[stored["bar_ts"] > last - timedelta(minutes=15 * 300)]
                ref = fetch_reference(product, (last + timedelta(minutes=15)).to_pydatetime(), http=http)
                report.results += reference.check_clock(spec.key, window, ref)
            except Exception as exc:  # noqa: BLE001
                report.results.append(CheckResult(spec.key, "UTC clock vs Coinbase", Status.SKIP,
                                                  f"couldn't reach Coinbase ({type(exc).__name__}) — "
                                                  "needs internet; run on your PC"))
    if use_reference and not any(s.key in reference.PRODUCTS for s in cfg.symbols):
        report.results.append(CheckResult("-", "UTC clock vs Coinbase", Status.SKIP,
                                          "add BTCUSD or ETHUSD to QTX_SYMBOLS to check the broker clock"))
    return report
