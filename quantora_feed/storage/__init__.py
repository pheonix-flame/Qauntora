"""Storage-backend factory. Moving to Oracle = add storage/oracle.py and a branch here."""
from __future__ import annotations

from config import FeedConfig
from storage.base import Store
from storage.preview import PreviewStore


def open_store(cfg: FeedConfig, dry_run: bool = False) -> Store:
    if dry_run:
        return PreviewStore(cfg.preview_dir)
    from storage.sqlserver import SqlServerStore  # lazy: pyodbc only needed for real runs
    return SqlServerStore(cfg.db.conn_str)


__all__ = ["Store", "open_store"]
