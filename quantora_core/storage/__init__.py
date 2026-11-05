"""Storage factory: Oracle by default, or local files with --csv-dir."""
from __future__ import annotations

from pathlib import Path

from config import CoreConfig
from storage.base import CoreStore


def open_store(cfg: CoreConfig, csv_dir: str | Path | None = None) -> CoreStore:
    if csv_dir:
        from storage.local import LocalCoreStore
        return LocalCoreStore(Path(csv_dir), cfg.output_dir)
    from storage.oracle import OracleCoreStore
    return OracleCoreStore(cfg.db)


__all__ = ["CoreStore", "open_store"]
