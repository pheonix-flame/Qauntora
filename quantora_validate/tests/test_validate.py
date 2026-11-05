import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main  # noqa: E402
from config import DBConfig, load_config  # noqa: E402
from storage.local import LocalValidateStore  # noqa: E402
from storage.oracle import OracleValidateStore  # noqa: E402
from triple_barrier import label, tier2_barriers  # noqa: E402
from validate import report, run_once  # noqa: E402

T0 = pd.Timestamp("2026-01-05 00:00")  # a Monday


def bars(rows, start=T0):
    ts = pd.date_range(start, periods=len(rows), freq="15min")
    return pd.DataFrame({"bar_ts": ts, "open_price": [r[0] for r in rows], "high_price": [r[1] for r in rows],
                         "low_price": [r[2] for r in rows], "close_price": [r[3] for r in rows]})


def flat(n, px=100.0):
    return [(px, px + 0.1, px - 0.1, px)] * n


# -- triple barrier ----------------------------------------------------------------------
def test_win_loss_and_r():
    up = bars([(100, 100.5, 99.5, 100.2), (100.2, 102.5, 100, 102)] + flat(20, 102))
    w = label(up, T0, 1, 100, 99, 102, 120, True)
    assert (w.outcome, w.r_multiple, w.resolved_price, w.resolved_at) == ("WIN", 2.0, 102, T0 + pd.Timedelta(minutes=30))
    down = bars([(100, 100.2, 98.8, 99)] + flat(20, 99))
    assert label(down, T0, 1, 100, 99, 102, 120, True).outcome == "LOSS"
    assert label(down, T0, -1, 100, 101, 98, 120, True).outcome == "TIMEOUT"   # short: neither hit within 2h


def test_same_bar_both_barriers_is_a_loss():
    assert label(bars([(100, 103, 98, 101)] + flat(20, 101)), T0, 1, 100, 99, 102, 120, True).outcome == "LOSS"


def test_gap_through_stop_resolves_at_worse_open():
    lab = label(bars([(100, 100.1, 99.9, 100), (97, 97.2, 96.8, 97)] + flat(20, 97)), T0, 1, 100, 99, 102, 120, True)
    assert lab.outcome == "LOSS" and lab.resolved_price == 97 and lab.r_multiple == -3.0


def test_timeout_at_deadline_open():
    lab = label(bars(flat(20)), T0, 1, 100, 99, 102, 120, True)
    assert lab.outcome == "TIMEOUT" and lab.resolved_at == T0 + pd.Timedelta(minutes=120) and lab.r_multiple == 0


def test_pending_until_resolved_or_deadline_passes():
    lab = label(bars(flat(4)), T0, 1, 100, 99, 102, 120, True)
    assert lab.outcome is None and "still open" in lab.reason


def test_hole_in_24_7_prices_is_not_guessed():
    before = bars(flat(3))
    after = bars(flat(10), start=T0 + pd.Timedelta(hours=2))
    m15 = pd.concat([before, after], ignore_index=True)
    lab = label(m15, T0, 1, 100, 99, 102, 600, always_open=True)
    assert lab.outcome is None and "gap" in lab.reason
    # the same hole on a market that closes (forex) is normal — labelled
    assert label(m15, T0, 1, 100, 99, 102, 240, always_open=False).outcome == "TIMEOUT"


def test_invalid_barriers_rejected():
    assert label(bars(flat(10)), T0, 1, 100, 101, 102, 120, True).outcome is None  # stop above a long's entry


def test_tier2_barriers_from_snapshot():
    assert tier2_barriers({"close": 100.0, "atr": 2.0}, -1, 1.5, 3.0) == (100.0, 103.0, 94.0)
    assert tier2_barriers({"close": 100.0}, 1, 1.5, 3.0) is None


# -- full run on Core-format files --------------------------------------------------------------
def core_signal(sid, tier, direction, ts, entry=None, stop=None, target=None, prev="WATCHING", **snap):
    snapshot = {"close_ts": str(ts), "close": 100.0, "atr": 1.0, "prev_tier": prev} | snap
    return {"signal_id": sid, "symbol": "V75", "mode": "SCALP", "tier": tier, "direction": direction,
            "score": 1.0, "entry_price": entry, "stop_loss": stop, "take_profit": target, "lot_size": None,
            "indicator_snapshot_json": json.dumps(snapshot), "created_at": str(ts)}


@pytest.fixture
def local(tmp_path):
    feed, core = tmp_path / "feed", tmp_path / "core"
    feed.mkdir(), core.mkdir()
    m15 = bars([(100, 100.5, 99.5, 100.2), (100.2, 101.6, 100, 101.5)] + flat(30, 101.5))
    m15.assign(symbol="V75", asset_class="SYNTHETIC", timeframe="M15", volume=1, price_side="BID").to_csv(
        feed / "ohlcv_V75_M15.csv", index=False)
    rows = [core_signal(1, "CONFIRMED", "BUY", T0, 100.0, 99.0, 101.5),          # TP hit in bar 2
            core_signal(2, "FORMING", "BUY", T0),                                 # tier 2 entry: 1.0/1.5 ATR -> WIN
            core_signal(3, "FORMING", "BUY", T0, prev="CONFIRMED", prev_direction="BUY"),  # decay: not an entry
            core_signal(4, "WATCHING", None, T0),
            core_signal(5, "CONFIRMED", "SELL", T0 + pd.Timedelta(hours=7, minutes=30), 101.5, 102.5, 100.0)]  # data ends before its deadline
    pd.DataFrame(rows).to_csv(core / "signals.csv", index=False)
    cfg = load_config()
    from dataclasses import replace
    cfg = replace(cfg, output_dir=tmp_path / "out")
    return cfg, LocalValidateStore(feed, core, cfg.output_dir)


def test_run_labels_what_it_can_and_is_idempotent(local):
    cfg, st = local
    s = run_once(st, cfg)
    assert s.labelled == 2 and s.by_outcome == {"CONFIRMED WIN": 1, "FORMING WIN": 1}
    assert any("still open" in k for k in s.pending)
    assert any("isn't an entry" in k for k in s.skipped)
    again = run_once(st, cfg)
    assert again.labelled == 0                       # labels are written once, never rewritten
    out = pd.read_csv(st.outcomes_path)
    assert sorted(out["signal_id"]) == [1, 2] and set(out["outcome"]) == {"WIN"}
    rep = report(st.outcomes_report())
    assert set(rep["tier"]) == {"CONFIRMED", "FORMING"}


def test_cli_local_mode(local, capsys, monkeypatch, tmp_path):
    cfg, st = local
    monkeypatch.setattr(main, "load_config", lambda *_: cfg)
    args = ["--csv-dir", str(st.feed_dir), "--core-dir", str(st.core_dir)]
    assert main.main(["check", *args]) == 0
    assert main.main(["once", *args]) == 0
    assert main.main(["report", *args]) == 0
    out = capsys.readouterr().out
    assert "Labelled 2" in out and "Tier 3" in out and "not meaningful yet" in out


# -- Oracle store against a fake driver ---------------------------------------------------------
def test_oracle_store_is_insert_only():
    calls = []

    class Cur:
        rowcount = 1

        def setinputsizes(self, *a):
            pass

        def executemany(self, sql, rows):
            calls.append((" ".join(sql.split()), rows))

        def execute(self, sql, params=None):
            calls.append((" ".join(sql.split()), params))

        def fetchall(self):
            return []

    conn = SimpleNamespace(cursor=lambda: Cur(), commit=lambda: None, close=lambda: None, version="23")
    st = OracleValidateStore(DBConfig("q", "pw", "d"),
                             driver=SimpleNamespace(connect=lambda **k: conn, DB_TYPE_TIMESTAMP="TS"))
    st.insert_outcomes([{"signal_id": 9, "outcome": "WIN", "r_multiple": 2.0, "resolved_price": 1.0,
                         "resolved_at": T0}])
    sql, rows = calls[-1]
    assert sql.startswith("INSERT INTO qtx_signal_outcomes") and "NOT EXISTS" in sql and rows[0][0] == 9
    st.unlabelled_signals()
    assert "trade_mode" in calls[-1][0] and not any(c[0].startswith(("UPDATE", "DELETE")) for c in calls)
