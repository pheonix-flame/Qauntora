"""Engine: live vs replay consistency, idempotency, catch-up, data guards, storage, CLI."""
import json
from dataclasses import replace
from types import SimpleNamespace

import pandas as pd
import pytest

import main
from config import DBConfig
from conftest import regime_walk, write_feed_csv
from diagnose import collinearity
from engine import evaluate_pair, replay
from storage.local import LocalCoreStore
from storage.oracle import OracleCoreStore


@pytest.fixture
def setup(tmp_path, cfg):
    csv = tmp_path / "csv"
    csv.mkdir()
    m15 = regime_walk(days=300)
    write_feed_csv(csv, m15)
    return cfg, csv, m15


def test_live_bar_by_bar_logs_exactly_what_replay_finds(setup, tmp_path):
    """The strongest guarantee: running live every bar == replaying history."""
    cfg, csv, m15 = setup
    cfg = replace(cfg, modes={**cfg.modes, "SWING": replace(cfg.modes["SWING"], lookback_bars=5000)})
    with LocalCoreStore(csv, tmp_path / "replay_out") as st:
        rep, _ = replay(st, cfg, "V75", "SWING")
    rep = rep[rep["signal_ts"] >= rep["signal_ts"].iloc[-1] - pd.Timedelta(days=60)]  # last 60 days

    live = LocalCoreStore(csv, tmp_path / "live_out")
    start = rep["signal_ts"].iloc[0] - pd.Timedelta(hours=4)
    evaluate_pair(live, cfg, "V75", "SWING", (start + pd.Timedelta(minutes=1)).to_pydatetime())  # seed state
    first_logged = len(live._new_signals)
    t = start + pd.Timedelta(hours=4)
    while t <= rep["signal_ts"].iloc[-1]:
        evaluate_pair(live, cfg, "V75", "SWING", (t + pd.Timedelta(minutes=1)).to_pydatetime())
        t += pd.Timedelta(hours=4)
    live.commit()
    logged = pd.read_csv(live.signals_path).iloc[first_logged:]
    got = [(json.loads(s)["close_ts"], tier, d) for s, tier, d in
           zip(logged["indicator_snapshot_json"], logged["tier"], logged["direction"].where(logged["tier"] != "WATCHING"))]
    want = [(str(ts), tier, d if isinstance(d, str) else None) for ts, tier, d in
            zip(rep["signal_ts"], rep["tier"], rep["direction"])]
    norm = lambda rows: [(a, b, c if isinstance(c, str) else None) for a, b, c in rows]  # noqa: E731
    assert norm(got) == norm(want)
    assert len(want) > 5


def test_rerun_on_same_bar_is_idempotent(setup, tmp_path):
    cfg, csv, m15 = setup
    now = (m15["bar_ts"].iloc[-1] + pd.Timedelta(minutes=16)).to_pydatetime()
    st = LocalCoreStore(csv, tmp_path / "out")
    a = evaluate_pair(st, cfg, "V75", "SWING", now)
    b = evaluate_pair(st, cfg, "V75", "SWING", now)
    assert a.status == "evaluated" and b.status == "already evaluated" and b.new_signals == 0


def test_catch_up_after_downtime_logs_missed_transitions(setup, tmp_path):
    cfg, csv, m15 = setup
    end = m15["bar_ts"].iloc[-1] + pd.Timedelta(minutes=15)
    st = LocalCoreStore(csv, tmp_path / "out")
    evaluate_pair(st, cfg, "V75", "SWING", (end - pd.Timedelta(days=8)).to_pydatetime())
    with LocalCoreStore(csv, tmp_path / "r") as r:
        rep, _ = replay(r, cfg, "V75", "SWING")
    expected = rep[(rep["signal_ts"] > end - pd.Timedelta(days=8)) & (rep["signal_ts"] <= end)]
    res = evaluate_pair(st, cfg, "V75", "SWING", end.to_pydatetime())
    assert res.new_signals == len(expected) > 0


def test_waits_for_feed_when_last_bar_incomplete(setup, tmp_path):
    cfg, csv, m15 = setup
    # drop the final M15 bar of the newest H4 bar, then evaluate right after that bar's close
    cut = m15[m15["bar_ts"] < "2026-11-04 11:45"]
    write_feed_csv(csv, cut)
    st = LocalCoreStore(csv, tmp_path / "out")
    res = evaluate_pair(st, cfg, "V75", "SWING", pd.Timestamp("2026-11-04 12:01").to_pydatetime())
    assert res.status == "waiting for Feed"


def test_stale_feed_warns(setup, tmp_path):
    cfg, csv, m15 = setup
    st = LocalCoreStore(csv, tmp_path / "out")
    res = evaluate_pair(st, cfg, "V75", "SWING", pd.Timestamp("2026-11-04 18:00").to_pydatetime())
    assert "Feed running" in res.warning


def test_no_data(cfg, tmp_path):
    (tmp_path / "csv").mkdir()
    res = evaluate_pair(LocalCoreStore(tmp_path / "csv", tmp_path / "o"), cfg, "V75", "SWING",
                        pd.Timestamp("2026-11-04").to_pydatetime())
    assert res.status == "no data"


# -- Oracle store against a fake driver ----------------------------------------------------
class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def setinputsizes(self, **kw):
        pass

    def var(self, typ):
        return SimpleNamespace(getvalue=lambda: [41])

    def execute(self, sql, params=None, **kw):
        self.conn.calls.append((" ".join(sql.split()), params, kw))
        self._rows = self.conn.result

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


@pytest.fixture
def oracle():
    conn = SimpleNamespace(calls=[], result=[], version="23.26", commit=lambda: None, close=lambda: None)
    conn.cursor = lambda: FakeCursor(conn)
    drv = SimpleNamespace(connect=lambda **k: conn, DB_TYPE_CLOB="CLOB", NUMBER="NUMBER")
    return OracleCoreStore(DBConfig("quantora", "pw", "dsn"), driver=drv), conn


def test_oracle_writes_fit_the_schema(oracle):
    store, conn = oracle
    store.save_state("V75", "SWING", "FORMING", "SELL", 0.756, {"a": 1}, "x" * 500)
    sql, _, kw = conn.calls[-1]
    assert sql.startswith("MERGE INTO qtx_signal_state") and "SYS_EXTRACT_UTC" in sql
    assert kw["direction"] == "SELL" and len(kw["direction"]) <= 4 and len(kw["notes"]) == 400
    assert kw["score"] == 0.76 and json.loads(kw["snap"]) == {"a": 1}
    sid = store.append_signal("V75", "SWING", "CONFIRMED", "BUY", 1.0, 100.0, 98.5, 103.0, {"b": 2})
    sql, _, kw = conn.calls[-1]
    assert sid == 41 and sql.startswith("INSERT INTO qtx_signals") and "lot_size" in sql
    assert not any(c[0].startswith(("UPDATE qtx_signals", "DELETE")) for c in conn.calls)  # append-only


def test_oracle_reads_approved_config_clob(oracle, cfg):
    store, conn = oracle
    clob = SimpleNamespace(read=lambda: json.dumps({"weights": {"location": 0.5}, "params": {"adx_min": 25}}))
    conn.result = [(7, clob, 0.55, 0.85)]
    sc = store.approved_config("V75", "SWING", cfg.defaults)
    assert sc.version_id == 7 and sc.tier3_threshold == 0.85 and sc.params["adx_min"] == 25.0
    assert "approved_flag = 'Y'" in conn.calls[-1][0]
    conn.result = []
    assert store.approved_config("V75", "SWING", cfg.defaults) is None


def test_oracle_needs_password():
    with pytest.raises(ValueError, match="ORACLE_PASSWORD"):
        OracleCoreStore(DBConfig("q", "", "d"), driver=SimpleNamespace(connect=lambda **k: None))


# -- diagnose ------------------------------------------------------------------------------
def test_collinearity_flags_duplicate_votes():
    n = 300
    s = pd.Series([1.0, -1.0, 1.0, 1.0, -1.0] * (n // 5))
    ev = pd.DataFrame({"ema_fast": s, "ema_slow": 0.0, "adx": 30.0, "plus_di": s, "minus_di": 0.0,
                       "rsi": 50 + 10 * s, "pct_b": 0.5 + 0.3 * pd.Series(range(n)).mod(3).sub(1), "warm": True})
    res = collinearity(ev, {"adx_min": 20}, crypto=False)
    assert res["effective_independent"] < 2.5
    assert {"trend", "strength", "momentum"} & set(res["flagged_vif"])


# -- CLI -----------------------------------------------------------------------------------
def test_cli_once_status_replay(setup, tmp_path, capsys, monkeypatch):
    cfg, csv, _ = setup
    monkeypatch.setattr(main, "load_config", lambda *_: cfg)
    monkeypatch.setattr(main, "utc_now", lambda: pd.Timestamp("2026-11-04 12:01").to_pydatetime())
    assert main.main(["once", "--csv-dir", str(csv)]) == 0
    assert main.main(["status", "--csv-dir", str(csv)]) == 0
    out = tmp_path / "rep.csv"
    assert main.main(["replay", "--symbol", "V75", "--csv-dir", str(csv), "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "V75 SWING" in text and "core_signals" in text
    df = pd.read_csv(out)
    assert {"signal_ts", "tier", "direction", "is_entry", "entry", "stop", "target"} <= set(df.columns)
