"""Result type shared by every verification check."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Status(str, Enum):
    PASS = "PASS"
    WARN = "WARN"   # worth a look, often benign (holiday gap, feed not running right now)
    FAIL = "FAIL"   # the stored data is wrong or can't be trusted
    SKIP = "SKIP"   # check couldn't run (no MT5, no network, symbol not configured)


@dataclass(frozen=True)
class CheckResult:
    symbol: str
    check: str
    status: Status
    detail: str
