"""Local backend for --csv-dir / --dry-run: no database needed.

Reads Feed's --dry-run CSVs (ohlcv_<SYMBOL>_M15.csv, news_events.csv).
Writes state to core_output/signal_state.json and the signal log to
core_output/signals.csv — same fields as the Oracle tables.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from config import SignalConfig
from storage.base import CoreStore, tidy_bars

SIGNAL_COLUMNS = ["signal_id", "symbol", "mode", "tier", "direction", "score", "entry_price", "stop_loss",
                  "take_profit", "lot_size", "indicator_snapshot_json", "created_at"]


class LocalCoreStore(CoreStore):
    def __init__(self, csv_dir: Path, out_dir: Path) -> None:
        self.csv_dir, self.out_dir = Path(csv_dir), Path(out_dir)
        if not self.csv_dir.is_dir():
            raise FileNotFoundError(f"CSV folder not found: {self.csv_dir}")
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.out_dir / "signal_state.json"
        self.signals_path = self.out_dir / "signals.csv"
        self._states = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}
        self._new_signals: list[dict] = []

    def describe(self) -> str:
        return f"local files: bars/news from {self.csv_dir}, state/signals in {self.out_dir}"

    def load_m15(self, symbol: str, since: datetime | None = None) -> pd.DataFrame:
        path = self.csv_dir / f"ohlcv_{symbol}_M15.csv"
        if not path.exists():
            return tidy_bars(pd.DataFrame(columns=["bar_ts", "open_price", "high_price", "low_price",
                                                   "close_price", "volume"]))
        df = tidy_bars(pd.read_csv(path))
        return df[df["bar_ts"] >= pd.Timestamp(since)].reset_index(drop=True) if since else df

    def news_events(self, start: datetime, end: datetime) -> pd.DataFrame:
        path = self.csv_dir / "news_events.csv"
        if not path.exists():
            return pd.DataFrame(columns=["event_ts", "currency", "impact", "title"])
        ev = pd.read_csv(path, parse_dates=["event_ts"])
        return ev[(ev["event_ts"] >= pd.Timestamp(start)) & (ev["event_ts"] <= pd.Timestamp(end))]

    def approved_config(self, symbol: str, mode: str, base: SignalConfig) -> SignalConfig | None:
        return None  # approvals live in the database only

    def get_state(self, symbol: str, mode: str) -> dict | None:
        return self._states.get(f"{symbol}|{mode}")

    def list_states(self) -> pd.DataFrame:
        rows = [{"symbol": k.split("|")[0], "mode": k.split("|")[1], **{x: v[x] for x in
                 ("tier", "direction", "score", "notes", "updated_at")}} for k, v in self._states.items()]
        return pd.DataFrame(rows, columns=["symbol", "mode", "tier", "direction", "score", "notes", "updated_at"])

    def save_state(self, symbol, mode, tier, direction, score, snapshot, notes) -> None:
        self._states[f"{symbol}|{mode}"] = {
            "tier": tier, "direction": direction, "score": round(float(score), 2), "snapshot": snapshot,
            "notes": (notes or "")[:400], "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")}

    def append_signal(self, symbol, mode, tier, direction, score, entry, stop, target, snapshot) -> int:
        existing = len(pd.read_csv(self.signals_path)) if self.signals_path.exists() else 0
        sid = existing + len(self._new_signals) + 1
        self._new_signals.append({
            "signal_id": sid, "symbol": symbol, "mode": mode, "tier": tier, "direction": direction,
            "score": round(float(score), 2), "entry_price": entry, "stop_loss": stop, "take_profit": target,
            "lot_size": None, "indicator_snapshot_json": json.dumps(snapshot),
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")})
        return sid

    def commit(self) -> None:
        self.state_path.write_text(json.dumps(self._states, indent=2))
        if self._new_signals:
            pd.DataFrame(self._new_signals, columns=SIGNAL_COLUMNS).to_csv(
                self.signals_path, mode="a", header=not self.signals_path.exists(), index=False)
            self._new_signals = []

    def close(self) -> None:
        self.commit()
