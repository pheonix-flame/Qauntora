"""Collinearity check (spec Section 5): are the indicators independent opinions?

Each dimension is discretised to a per-bar vote in {-1, 0, +1}:
  trend     sign(EMA fast - EMA slow)
  strength  sign(+DI - -DI) when ADX >= adx_min, else 0
  momentum  RSI > 50 -> +1, < 50 -> -1   (or MACD histogram sign)
  location  %b > 0.5 -> +1 (upper half of the bands), < 0.5 -> -1
  volume    sign(OBV slope)              (crypto only)
Then: pairwise correlation, VIF per vote (> 5 = redundant), and the effective
number of independent dimensions (participation ratio of the correlation
matrix's eigenvalues): "these N indicators measure ~X independent things".

Calibrate (step 9) will run this every cycle and store it in
backtest_metrics_json; Core exposes it now so you can look before approving.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

RHO_FLAG = 0.7
VIF_FLAG = 5.0


def votes(evals: pd.DataFrame, params: dict, crypto: bool) -> pd.DataFrame:
    v = pd.DataFrame(index=evals.index)
    v["trend"] = np.sign(evals["ema_fast"] - evals["ema_slow"])
    v["strength"] = np.where(evals["adx"] >= params["adx_min"], np.sign(evals["plus_di"] - evals["minus_di"]), 0)
    mom = evals["macd_hist"] if "macd_hist" in evals else evals["rsi"] - 50
    v["momentum"] = np.sign(mom)
    v["location"] = np.sign(evals["pct_b"] - 0.5)
    if crypto:
        v["volume"] = np.sign(evals["obv_slope"])
    return v[evals["warm"]].dropna().astype(float)


def vif(x: pd.DataFrame) -> dict[str, float]:
    out = {}
    for col in x.columns:
        y = x[col].to_numpy()
        others = x.drop(columns=col).to_numpy()
        a = np.column_stack([np.ones(len(y)), others])
        coef, *_ = np.linalg.lstsq(a, y, rcond=None)
        resid = y - a @ coef
        ss_tot = ((y - y.mean()) ** 2).sum()
        r2 = 1 - (resid ** 2).sum() / ss_tot if ss_tot > 0 else 1.0
        out[col] = float("inf") if r2 >= 1 else 1 / (1 - r2)
    return out


def collinearity(evals: pd.DataFrame, params: dict, crypto: bool) -> dict:
    v = votes(evals, params, crypto)
    v = v.loc[:, v.std() > 0]  # a vote that never changes carries no information
    if len(v) < 30 or v.shape[1] < 2:
        return {"bars": len(v), "note": "not enough varying data for a collinearity check"}
    corr = v.corr()
    eig = np.linalg.eigvalsh(corr.to_numpy())
    effective = float(eig.sum() ** 2 / (eig ** 2).sum())
    pairs = [(a, b, float(corr.loc[a, b])) for i, a in enumerate(corr.columns) for b in corr.columns[i + 1:]]
    vifs = vif(v)
    return {
        "bars": len(v), "dimensions": list(v.columns), "effective_independent": round(effective, 2),
        "correlation": corr.round(3).to_dict(),
        "flagged_pairs": [{"a": a, "b": b, "rho": round(r, 3)} for a, b, r in pairs if abs(r) > RHO_FLAG],
        "vif": {k: (round(x, 2) if np.isfinite(x) else None) for k, x in vifs.items()},
        "flagged_vif": [k for k, x in vifs.items() if not np.isfinite(x) or x > VIF_FLAG],
    }
