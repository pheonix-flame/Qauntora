"""What Notify has already sent — a small local JSON file, written atomically.

Delivery is at-least-once: a position only moves forward after Telegram
accepted the message, so a crash or network failure means a retry, never a
silently lost alert.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class NotifyState:
    initialized: bool = False
    last_signal_id: int = 0
    last_outcome_id: int = 0
    # problem key -> {"text", "first_seen", "last_sent"} (ISO strings, UTC)
    problems: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "NotifyState":
        if not path.exists():
            return cls()
        return cls(**json.loads(path.read_text()))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2))
        os.replace(tmp, path)  # atomic: never a half-written state file
