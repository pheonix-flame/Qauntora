"""Quantora Validate — configuration (own copy; engines share no code)."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
REPO_DIR = BASE_DIR.parent
load_dotenv(BASE_DIR / ".env")
load_dotenv(REPO_DIR / ".env")

MODES = ("SCALP", "SWING", "LONG")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass(frozen=True)
class DBConfig:
    user: str
    password: str
    dsn: str
    wallet_dir: str = ""
    wallet_password: str = ""


@dataclass(frozen=True)
class ModeBarrier:
    max_hold_minutes: int
    sl_atr: float
    tp_atr: float


@dataclass(frozen=True)
class ValidateConfig:
    db: DBConfig
    modes: dict[str, ModeBarrier]
    tier2_hypothetical: bool = True
    interval_minutes: int = 60
    output_dir: Path = BASE_DIR / "validate_output"


def load_config(settings_file: Path | None = None) -> ValidateConfig:
    with open(settings_file or BASE_DIR / "settings.toml", "rb") as fh:
        raw = tomllib.load(fh)
    modes = {m: ModeBarrier(**raw["modes"][m]) for m in MODES if m in raw.get("modes", {})}
    return ValidateConfig(
        db=DBConfig(user=_env("ORACLE_USER", "quantora"), password=_env("ORACLE_PASSWORD"),
                    dsn=_env("ORACLE_DSN", "localhost:1521/FREEPDB1"),
                    wallet_dir=_env("ORACLE_WALLET_DIR"), wallet_password=_env("ORACLE_WALLET_PASSWORD")),
        modes=modes,
        tier2_hypothetical=bool(raw.get("label", {}).get("tier2_hypothetical", True)),
        interval_minutes=int(raw.get("run", {}).get("interval_minutes", 60)),
        output_dir=Path(_env("VALIDATE_OUTPUT_DIR")) if _env("VALIDATE_OUTPUT_DIR") else BASE_DIR / "validate_output",
    )
