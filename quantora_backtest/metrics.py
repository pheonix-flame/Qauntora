"""Performance metrics (spec Section 8), computed on R-multiples.

Equity simulation: fixed-fractional — each trade risks `risk_pct` of current
equity, so equity *= (1 + risk_pct/100 * R). Annualisation uses the number of
trades per year actually observed in the tested period.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def _f(x) -> float | None:
    return None if x is None or not np.isfinite(x) else round(float(x), 4)


def compute(trades: pd.DataFrame, risk_pct: float, period_days: float) -> dict:
    n = len(trades)
    out: dict = {"trades": n}
    if n == 0:
        return out | {k: None for k in ["win_rate", "avg_win_r", "avg_loss_r", "expectancy_r", "profit_factor",
                                         "sharpe", "sortino", "total_return_pct", "max_drawdown_pct", "calmar",
                                         "ulcer_index", "trades_per_year"]}
    r = trades["r"].to_numpy(float)
    wins, losses = r[r > 0], r[r < 0]
    years = max(period_days, 1) / 365.25
    tpy = n / years
    std = r.std(ddof=1) if n > 1 else float("nan")
    downside = math.sqrt(np.mean(np.minimum(r, 0) ** 2))

    growth = np.clip(1 + risk_pct / 100 * r, 0, None)
    equity = np.cumprod(growth)
    peak = np.maximum.accumulate(np.r_[1.0, equity])[1:]
    dd = equity / peak - 1
    max_dd = -dd.min() * 100
    cagr = (equity[-1] ** (1 / years) - 1) * 100 if equity[-1] > 0 else -100.0

    counts = trades["outcome"].value_counts()
    return out | {
        "wins": int(counts.get("WIN", 0)), "losses": int(counts.get("LOSS", 0)),
        "timeouts": int(counts.get("TIMEOUT", 0)),
        "win_rate": _f(len(wins) / n),
        "avg_win_r": _f(wins.mean()) if len(wins) else None,
        "avg_loss_r": _f(losses.mean()) if len(losses) else None,
        "expectancy_r": _f(r.mean()),
        "profit_factor": _f(wins.sum() / -losses.sum()) if len(losses) else None,
        "sharpe": _f(r.mean() / std * math.sqrt(tpy)) if std and np.isfinite(std) and std > 0 else None,
        "sortino": _f(r.mean() / downside * math.sqrt(tpy)) if downside > 0 else None,
        "total_return_pct": _f((equity[-1] - 1) * 100),
        "cagr_pct": _f(cagr),
        "max_drawdown_pct": _f(max_dd),
        "calmar": _f(cagr / max_dd) if max_dd > 0 else None,
        "ulcer_index": _f(math.sqrt(np.mean((dd * 100) ** 2))),
        "trades_per_year": _f(tpy),
        "avg_hold_hours": _f(trades["minutes_held"].mean() / 60),
    }
