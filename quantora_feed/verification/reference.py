"""Independent check of the *timestamps* against a different company's prices.

The MT5 checks can't catch a wrong broker-clock conversion, because the bars
and the conversion both come from the same place. So we compare Deriv's
BTCUSD/ETHUSD against Coinbase's public 15-minute candles (no API key).

BTC moves the same way on every venue within seconds, so the 15-minute returns
of the two series line up best at lag 0 *only if our UTC timestamps are right*.
If the best match is at lag +8 bars, our bar times are 2 hours late — and since
every symbol shares the same broker clock, that error affects all of them.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests

from verification.base import CheckResult, Status

log = logging.getLogger("quantora.feed")

COINBASE_URL = "https://api.exchange.coinbase.com/products/{product}/candles"
PRODUCTS = {"BTCUSD": "BTC-USD", "ETHUSD": "ETH-USD"}
MAX_LAG_BARS = 12        # search ±3 hours
MIN_OVERLAP = 50
MIN_CORR = 0.7
MAX_PRICE_GAP = 0.01     # CFD vs spot: 1%


def fetch_coinbase(product: str, end_utc: datetime, bars: int = 300,
                   http: requests.Session | None = None) -> pd.Series:
    """Up to 300 closed 15-minute closes ending at end_utc, indexed by naive-UTC bar start."""
    http = http or requests.Session()
    start = end_utc - timedelta(minutes=15 * bars)
    resp = http.get(COINBASE_URL.format(product=product), timeout=30,
                    params={"granularity": 900, "start": start.isoformat() + "Z", "end": end_utc.isoformat() + "Z"},
                    headers={"User-Agent": "quantora-verify/0.1"})
    resp.raise_for_status()
    rows = resp.json()  # [[time, low, high, open, close, volume], ...] newest first
    s = pd.Series({pd.Timestamp(int(r[0]), unit="s"): float(r[4]) for r in rows}, name="close")
    return s.sort_index()


def best_lag(ours: pd.Series, ref: pd.Series, max_lag: int = MAX_LAG_BARS) -> tuple[int, float, float, int]:
    """Lag (in bars) at which our returns best match the reference.

    Returns (best_lag, corr_at_best, corr_at_zero, overlap_at_zero). Positive lag
    means our labels are later than the truth: our bar at t == reference at t - lag.
    """
    grid = pd.date_range(min(ours.index.min(), ref.index.min()), max(ours.index.max(), ref.index.max()),
                         freq="15min")
    r1 = np.log(ours.reindex(grid)).diff()
    r2 = np.log(ref.reindex(grid)).diff()
    corrs: dict[int, tuple[float, int]] = {}
    for k in range(-max_lag, max_lag + 1):
        pair = pd.concat([r1, r2.shift(k)], axis=1).dropna()
        if len(pair) >= MIN_OVERLAP:
            corrs[k] = (float(pair.iloc[:, 0].corr(pair.iloc[:, 1])), len(pair))
    if not corrs:
        return 0, float("nan"), float("nan"), 0
    k_best = max(corrs, key=lambda k: corrs[k][0])
    zero = corrs.get(0, (float("nan"), 0))
    return k_best, corrs[k_best][0], zero[0], zero[1]


def check_clock(symbol: str, stored: pd.DataFrame, ref: pd.Series) -> list[CheckResult]:
    ours = pd.Series(stored["close_price"].astype(float).to_numpy(), index=pd.to_datetime(stored["bar_ts"]))
    lag, corr_best, corr0, n = best_lag(ours, ref)
    out = []
    if not n:
        return [CheckResult(symbol, "UTC clock vs Coinbase", Status.SKIP, "too little overlap with Coinbase data")]
    if lag != 0 and corr_best - (corr0 if corr0 == corr0 else 0) > 0.1:
        direction = "late" if lag > 0 else "early"
        out.append(CheckResult(symbol, "UTC clock vs Coinbase", Status.FAIL,
                               f"best match at {abs(lag) * 15} min shift (corr {corr_best:.2f} vs {corr0:.2f} "
                               f"unshifted) — stored bar times are {abs(lag) * 15} min too {direction}. "
                               "Fix BROKER_TZ / BROKER_UTC_OFFSET_HOURS; affects ALL symbols."))
    elif corr0 < MIN_CORR:
        out.append(CheckResult(symbol, "UTC clock vs Coinbase", Status.WARN,
                               f"no shift beats 0, but correlation is weak ({corr0:.2f} over {n} bars)"))
    else:
        out.append(CheckResult(symbol, "UTC clock vs Coinbase", Status.PASS,
                               f"returns line up at 0 shift (corr {corr0:.2f} over {n} bars) — timestamps are true UTC"))
    joined = pd.concat([ours, ref], axis=1, join="inner").dropna()
    if len(joined):
        gap = float((joined.iloc[:, 0] / joined.iloc[:, 1] - 1).abs().median())
        status = Status.PASS if gap <= MAX_PRICE_GAP else Status.WARN
        out.append(CheckResult(symbol, "price level vs Coinbase", status,
                               f"median difference {gap:.3%} over {len(joined)} bars"))
    return out
