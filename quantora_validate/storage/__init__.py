"""Storage factory: Oracle by default, or local files (--csv-dir + --core-dir)."""
from __future__ import annotations

from pathlib import Path

from config import ValidateConfig
from storage.base import ValidateStore


def open_store(cfg: ValidateConfig, csv_dir=None, core_dir=None) -> ValidateStore:
    if csv_dir or core_dir:
        if not (csv_dir and core_dir):
            raise ValueError("Local mode needs both --csv-dir (Feed CSVs) and --core-dir (Core's core_output)")
        from storage.local import LocalValidateStore
        return LocalValidateStore(Path(csv_dir), Path(core_dir), cfg.output_dir)
    from storage.oracle import OracleValidateStore
    return OracleValidateStore(cfg.db)


__all__ = ["ValidateStore", "open_store"]
