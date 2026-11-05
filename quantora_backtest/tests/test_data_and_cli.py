from types import SimpleNamespace

import pandas as pd
import pytest

import main
from config import DBConfig
from data_sources.csv_dir import CsvBarSource
from data_sources.oracle import OracleBarSource
from synthetic import random_walk


def write_feed_csv(folder, symbol="V75", days=30):
    """Exactly the format Feed's --dry-run writes."""
    df = random_walk(days=days, seed=2).assign(symbol=symbol, asset_class="SYNTHETIC", timeframe="M15",
                                               price_side="BID")
    df.to_csv(folder / f"ohlcv_{symbol}_M15.csv", index=False)
    return df


def test_csv_source_reads_feed_preview_files(tmp_path):
    src_df = write_feed_csv(tmp_path)
    src = CsvBarSource(tmp_path)
    m15 = src.load_m15("V75")
    assert len(m15) == len(src_df) and m15["bar_ts"].is_monotonic_increasing
    inv = src.inventory()
    assert inv.iloc[0]["symbol"] == "V75" and inv.iloc[0]["bars"] == len(src_df)
    with pytest.raises(LookupError):
        src.load_m15("EURUSD")


def test_oracle_source_is_read_only(tmp_path):
    executed = []

    class Cur:
        arraysize = 0

        def execute(self, sql, params=None):
            executed.append(sql)

        def fetchall(self):
            return [(pd.Timestamp("2026-01-05 00:00"), 1.0, 2.0, 0.5, 1.5, 10.0)]

    conn = SimpleNamespace(version="23.26", cursor=lambda: Cur(), close=lambda: None)
    src = OracleBarSource(DBConfig("quantora", "pw", "x"), driver=SimpleNamespace(connect=lambda **k: conn))
    assert len(src.load_m15("V75")) == 1
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in executed)


def test_oracle_source_needs_password():
    with pytest.raises(ValueError, match="ORACLE_PASSWORD"):
        OracleBarSource(DBConfig("quantora", "", "x"), driver=SimpleNamespace(connect=lambda **k: None))


def test_run_refuses_without_costs(tmp_path, capsys):
    write_feed_csv(tmp_path)
    assert main.main(["run", "--symbol", "V75", "--csv-dir", str(tmp_path)]) == 2
    assert "No trading costs for V75" in capsys.readouterr().out


def test_check_reports_history_and_missing_costs(tmp_path, capsys):
    write_feed_csv(tmp_path, days=30)
    assert main.main(["check", "--csv-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "V75" in out and "NO" in out and "short" in out


def test_demo_runs(capsys):
    assert main.main(["demo", "--runs", "10"]) == 0
    assert "VERDICT" in capsys.readouterr().out
