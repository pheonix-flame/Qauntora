"""Local reader: Feed CSVs, Core's core_output, Validate's validate_output (no database)."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from storage.base import OUTCOME_COLS, SIGNAL_COLS, NotifyReader, as_json


class LocalReader(NotifyReader):
    def __init__(self, feed_dir, core_dir, validate_dir=None) -> None:
        self.feed_dir, self.core_dir = Path(feed_dir), Path(core_dir)
        self.validate_dir = Path(validate_dir) if validate_dir else None
        if not self.feed_dir.is_dir():  # Core/Validate folders may not exist yet: that just means "nothing yet"
            raise FileNotFoundError(f"Feed CSV folder not found: {self.feed_dir}")

    def describe(self) -> str:
        return f"local files: {self.feed_dir}, {self.core_dir}, {self.validate_dir or '(no Validate output)'}"

    def _signals(self) -> pd.DataFrame:
        p = self.core_dir / "signals.csv"
        if not p.exists():
            return pd.DataFrame(columns=SIGNAL_COLS)
        df = pd.read_csv(p).rename(columns={"mode": "trade_mode"})
        df["snapshot"] = df["indicator_snapshot_json"].map(as_json)
        return df[SIGNAL_COLS]

    def _outcomes(self) -> pd.DataFrame:
        p = self.validate_dir / "signal_outcomes.csv" if self.validate_dir else None
        if p is None or not p.exists():
            return pd.DataFrame(columns=["outcome_id", "signal_id", "outcome", "r_multiple", "resolved_price",
                                         "resolved_at"])
        df = pd.read_csv(p, parse_dates=["resolved_at"])
        df.insert(0, "outcome_id", range(1, len(df) + 1))  # file order = insert order
        return df

    def max_signal_id(self) -> int:
        s = self._signals()
        return int(s["signal_id"].max()) if len(s) else 0

    def signals_after(self, signal_id: int, limit: int = 500) -> pd.DataFrame:
        s = self._signals()
        return s[s["signal_id"] > signal_id].sort_values("signal_id").head(limit).reset_index(drop=True)

    def max_outcome_id(self) -> int:
        return len(self._outcomes())

    def outcomes_after(self, outcome_id: int, limit: int = 500) -> pd.DataFrame:
        o, s = self._outcomes(), self._signals()
        if o.empty:
            return pd.DataFrame(columns=OUTCOME_COLS)
        m = o[o["outcome_id"] > outcome_id].merge(s[["signal_id", "symbol", "trade_mode", "tier", "direction"]],
                                                  on="signal_id")
        return m[OUTCOME_COLS].head(limit).reset_index(drop=True)

    def feed_freshness(self) -> pd.DataFrame:
        rows = []
        for p in sorted(self.feed_dir.glob("ohlcv_*_M15.csv")):
            df = pd.read_csv(p, usecols=["bar_ts", "asset_class"], parse_dates=["bar_ts"])
            if len(df):
                rows.append((p.stem[len("ohlcv_"):-len("_M15")], df["asset_class"].iloc[0], df["bar_ts"].max()))
        return pd.DataFrame(rows, columns=["symbol", "asset_class", "last_bar_ts"])

    def core_states(self) -> pd.DataFrame:
        p = self.core_dir / "signal_state.json"
        if not p.exists():
            return pd.DataFrame(columns=["symbol", "trade_mode", "tier", "updated_at"])
        st = json.loads(p.read_text())
        return pd.DataFrame([(k.split("|")[0], k.split("|")[1], v["tier"], pd.Timestamp(v["updated_at"]))
                             for k, v in st.items()], columns=["symbol", "trade_mode", "tier", "updated_at"])

    def unlabelled_confirmed(self) -> pd.DataFrame:
        s, o = self._signals(), self._outcomes()
        s = s[(s["tier"] == "CONFIRMED") & ~s["signal_id"].isin(set(o["signal_id"]))]
        return pd.DataFrame({"signal_id": s["signal_id"], "symbol": s["symbol"], "trade_mode": s["trade_mode"],
                             "close_ts": s["snapshot"].map(lambda x: x.get("close_ts"))})
