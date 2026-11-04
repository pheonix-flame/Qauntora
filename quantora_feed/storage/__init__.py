"""Storage-backend factory.

Oracle is the current target. Another database (e.g. SQL Server, whose DDL is
kept in sql/ as a fallback) would be one new module implementing storage.base.Store
plus a branch here.
"""
from __future__ import annotations

from config import FeedConfig
from storage.base import Store
from storage.preview import PreviewStore


def open_store(cfg: FeedConfig, dry_run: bool = False) -> Store:
    if dry_run:
        return PreviewStore(cfg.preview_dir)
    from storage.oracle import OracleStore  # lazy: oracledb only needed for real runs
    return OracleStore(cfg.db)


__all__ = ["Store", "open_store"]
