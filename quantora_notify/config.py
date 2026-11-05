"""Quantora Notify — configuration (own copy; engines share no code).

Secrets come from the shared Qauntora/.env (quantora_notify/.env wins):
  TELEGRAM_BOT_TOKEN   from @BotFather (see README "Create your Telegram bot")
  TELEGRAM_CHAT_ID     from `python main.py chat-id`
"""
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

MODE_BAR_MINUTES = {"SCALP": 15, "SWING": 240, "LONG": 1440}
MAX_HOLD_MINUTES = {"SCALP": 45, "SWING": 20160, "LONG": 129600}  # keep equal to Validate's settings


def _env(name: str, default: str = "") -> str:
    v = os.getenv(name, default).strip()
    # an older .env template put comments after blank values; dotenv then reads the comment AS the value
    return default if v.startswith(("# >>>", "# optional")) else v


@dataclass(frozen=True)
class DBConfig:
    user: str
    password: str
    dsn: str
    wallet_dir: str = ""
    wallet_password: str = ""


@dataclass(frozen=True)
class TelegramConfig:
    token: str
    chat_id: str
    api_base: str = "https://api.telegram.org"   # overridable for testing


@dataclass(frozen=True)
class NotifyConfig:
    db: DBConfig
    telegram: TelegramConfig
    tier2: bool = True
    tier3: bool = True
    cancelled: bool = True
    outcomes: bool = True
    max_signal_age_bars: int = 2
    max_outcome_age_hours: int = 24
    feed_max_lag_minutes: int = 60
    core_grace_minutes: int = 30
    validate_grace_hours: int = 6
    repeat_every_hours: int = 3
    interval_seconds: int = 60
    state_dir: Path = BASE_DIR / "notify_state"


def load_config(settings_file: Path | None = None) -> NotifyConfig:
    with open(settings_file or BASE_DIR / "settings.toml", "rb") as fh:
        raw = tomllib.load(fh)
    a, h, r = raw.get("alerts", {}), raw.get("health", {}), raw.get("run", {})
    return NotifyConfig(
        db=DBConfig(user=_env("ORACLE_USER", "quantora"), password=_env("ORACLE_PASSWORD"),
                    dsn=_env("ORACLE_DSN", "localhost:1521/FREEPDB1"),
                    wallet_dir=_env("ORACLE_WALLET_DIR"), wallet_password=_env("ORACLE_WALLET_PASSWORD")),
        telegram=TelegramConfig(token=_env("TELEGRAM_BOT_TOKEN"), chat_id=_env("TELEGRAM_CHAT_ID"),
                                api_base=_env("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/")),
        tier2=bool(a.get("tier2", True)), tier3=bool(a.get("tier3", True)),
        cancelled=bool(a.get("cancelled", True)), outcomes=bool(a.get("outcomes", True)),
        max_signal_age_bars=int(a.get("max_signal_age_bars", 2)),
        max_outcome_age_hours=int(a.get("max_outcome_age_hours", 24)),
        feed_max_lag_minutes=int(h.get("feed_max_lag_minutes", 60)),
        core_grace_minutes=int(h.get("core_grace_minutes", 30)),
        validate_grace_hours=int(h.get("validate_grace_hours", 6)),
        repeat_every_hours=int(h.get("repeat_every_hours", 3)),
        interval_seconds=int(r.get("interval_seconds", 60)),
        state_dir=Path(_env("NOTIFY_STATE_DIR")) if _env("NOTIFY_STATE_DIR") else BASE_DIR / "notify_state",
    )
