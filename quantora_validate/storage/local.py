"""Local backend: Feed's --dry-run CSVs + Core's core_output/signals.csv -> validate_output/signal_outcomes.csv."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from storage.base import OUTCOME_COLUMNS, SIGNAL_COLUMNS, ValidateStore


class LocalValidateStore(ValidateStore):
    def __init__(self, feed_dir: Path, core_dir: Path, out_dir: Path) -> None:
        self.feed_dir, self.core_dir, self.out_dir = Path(feed_dir), Path(core_dir), Path(out_dir)
        for d, what in ((self.feed_dir, "Feed CSV"), (self.core_dir, "Core output")):
            if not d.is_dir():
                raise FileNotFoundError(f"{what} folder not found: {d}")
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.outcomes_path = self.out_dir / "signal_outcomes.csv"

    def describe(self) -> str:
        return f"local files: prices {self.feed_dir}, signals {self.core_dir}, outcomes {self.out_dir}"

    def _outcomes(self) -> pd.DataFrame:
        return pd.read_csv(self.outcomes_path) if self.outcomes_path.exists() else pd.DataFrame(columns=OUTCOME_COLUMNS)

    def _signals(self) -> pd.DataFrame:
        path = self.core_dir / "signals.csv"
        return pd.read_csv(path) if path.exists() else pd.DataFrame()

    def unlabelled_signals(self) -> pd.DataFrame:
        sig = self._signals()
        if sig.empty:
            return pd.DataFrame(columns=SIGNAL_COLUMNS)
        sig = sig.rename(columns={"mode": "trade_mode"})
        done = set(self._outcomes()["signal_id"])
        sig = sig[sig["tier"].isin(["CONFIRMED", "FORMING"]) & sig["direction"].isin(["BUY", "SELL"])
                  & ~sig["signal_id"].isin(done)].copy()
        sig["snapshot"] = sig["indicator_snapshot_json"].map(lambda s: json.loads(s) if isinstance(s, str) else {})
        return sig[SIGNAL_COLUMNS].reset_index(drop=True)

    def load_m15(self, symbol: str, start: datetime, end: datetime) -> tuple[pd.DataFrame, str]:
        path = self.feed_dir / f"ohlcv_{symbol}_M15.csv"
        if not path.exists():
            return pd.DataFrame(columns=["bar_ts", "open_price", "high_price", "low_price", "close_price"]), "SYNTHETIC"
        df = pd.read_csv(path, parse_dates=["bar_ts"])
        asset = str(df["asset_class"].iloc[0]) if "asset_class" in df and len(df) else "SYNTHETIC"
        df = df[(df["bar_ts"] >= pd.Timestamp(start)) & (df["bar_ts"] <= pd.Timestamp(end))]
        return df.sort_values("bar_ts").reset_index(drop=True), asset

    def insert_outcomes(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        done = set(self._outcomes()["signal_id"])
        new = [r for r in rows if r["signal_id"] not in done]
        if new:
            pd.DataFrame(new, columns=OUTCOME_COLUMNS).assign(
                evaluated_at=datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")).to_csv(
                self.outcomes_path, mode="a", header=not self.outcomes_path.exists(), index=False)
        return len(new)

    def outcomes_report(self) -> pd.DataFrame:
        out, sig = self._outcomes(), self._signals()
        if out.empty or sig.empty:
            return pd.DataFrame(columns=["symbol", "trade_mode", "tier", "outcome", "r_multiple", "resolved_at"])
        m = out.merge(sig.rename(columns={"mode": "trade_mode"}), on="signal_id")
        return m[["symbol", "trade_mode", "tier", "outcome", "r_multiple", "resolved_at"]]
