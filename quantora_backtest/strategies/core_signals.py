"""Quantora Core's signals, read from its replay file (data, not code).

Produce the file in quantora_core:
    python main.py replay --symbol V75 --mode SWING
then backtest it here:
    python main.py run --symbol V75 --mode SWING --strategy core_signals --signals <that file>

The engines stay independent (spec Section 15: no engine imports another):
Core writes a CSV of every tier change; this strategy only reads it.

Entries used:
  CONFIRMED (Tier 3)  transitions INTO confirmed (or a direction flip while confirmed)
  FORMING   (Tier 2)  transitions INTO forming from watching (or a flip) — the
                      hypothetical "acted early" entries for the Section 4 check.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from strategies.base import Strategy

REQUIRED = {"signal_ts", "symbol", "mode", "tier", "direction", "is_entry"}


class CoreSignals(Strategy):
    name = "core_signals"
    description = "Signals replayed by Quantora Core (needs --signals <replay CSV>)"
    param_grid: dict = {}

    def __init__(self, path: str | Path | None = None, tier: str = "CONFIRMED",
                 symbol: str | None = None, mode: str | None = None) -> None:
        if path is None:
            raise ValueError("core_signals needs --signals <file> (from `python main.py replay` in quantora_core)")
        self.path, self.tier, self.symbol, self.mode = Path(path), tier, symbol, mode
        if not self.path.exists():
            raise FileNotFoundError(f"Signals file not found: {self.path}")
        df = pd.read_csv(self.path)
        missing = REQUIRED - set(df.columns)
        if missing:
            raise ValueError(f"{self.path.name} isn't a Core replay file (missing columns {sorted(missing)})")
        df["signal_ts"] = pd.to_datetime(df["signal_ts"])
        for col, want in (("symbol", symbol), ("mode", mode)):
            have = set(df[col].dropna().unique())
            if want and have and have != {want}:
                raise ValueError(f"{self.path.name} holds {col} {sorted(have)}, but you asked for {want}")
        self._df = df

    def signals(self, bars: pd.DataFrame, params: dict) -> pd.DataFrame:
        df = self._df
        entry = df["is_entry"].astype(str).str.lower().isin(["true", "1"])
        pick = df[entry & (df["tier"] == self.tier) & df["direction"].isin(["BUY", "SELL"])]
        return pd.DataFrame({"signal_ts": pick["signal_ts"].to_numpy(),
                             "direction": pick["direction"].map({"BUY": 1, "SELL": -1}).to_numpy()})

    def tier_variant(self) -> "CoreSignals":
        """Same file, Tier 2 entries — for the 'does waiting for Tier 3 pay?' comparison."""
        return CoreSignals(self.path, "FORMING", self.symbol, self.mode)
