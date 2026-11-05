"""Reader factory: Oracle by default, or local files (--csv-dir + --core-dir [+ --validate-dir])."""
from __future__ import annotations

from config import NotifyConfig
from storage.base import NotifyReader


def open_reader(cfg: NotifyConfig, csv_dir=None, core_dir=None, validate_dir=None) -> NotifyReader:
    if csv_dir or core_dir:
        if not (csv_dir and core_dir):
            raise ValueError("Local mode needs --csv-dir (Feed CSVs) and --core-dir (Core output)")
        from storage.local import LocalReader
        return LocalReader(csv_dir, core_dir, validate_dir)
    from storage.oracle import OracleReader
    return OracleReader(cfg.db)


__all__ = ["NotifyReader", "open_reader"]
