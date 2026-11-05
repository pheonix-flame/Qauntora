"""Bar-source factory: Oracle by default, or a folder of Feed --dry-run CSVs."""
from __future__ import annotations

from pathlib import Path

from config import BacktestConfig
from data_sources.base import BAR_COLUMNS, BarSource


def open_source(cfg: BacktestConfig, csv_dir: str | Path | None = None) -> BarSource:
    if csv_dir:
        from data_sources.csv_dir import CsvBarSource
        return CsvBarSource(Path(csv_dir))
    from data_sources.oracle import OracleBarSource
    return OracleBarSource(cfg.db)


__all__ = ["BAR_COLUMNS", "BarSource", "open_source"]
