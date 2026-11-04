"""Quantora Feed — configuration.

Everything secret or machine-specific comes from `quantora_feed/.env`
(copy `.env.example` to `.env` and fill it in). Nothing here is shared with
any other engine — per the spec, each engine owns its own config.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
REPO_DIR = BASE_DIR.parent
load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    return int(raw) if raw else default


# ---------------------------------------------------------------------------
# Tradable universe (spec Section 3).
#   key           -> canonical name written to qtx_ohlcv.symbol
#   broker_symbol -> exact name in the Deriv MT5 terminal's Market Watch
#   asset_class   -> FOREX | SYNTHETIC | CRYPTO  (gold is grouped with FOREX)
#   source        -> which price_sources/ adapter serves it (default "mt5").
#                    Set QTX_SOURCE_<KEY>=ccxt later to move a symbol elsewhere.
# Broker names can differ by account type; `python main.py check` lists what
# your account actually has. Override any name in .env, e.g.
#   QTX_BROKER_NAME_V75=Volatility 75 (1s) Index
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SymbolSpec:
    key: str
    broker_symbol: str
    asset_class: str
    source: str = "mt5"


_DEFAULT_SYMBOLS: list[tuple[str, str, str]] = [
    # Synthetics — V75 is the recommended starting instrument (Section 13)
    ("V75", "Volatility 75 Index", "SYNTHETIC"),
    ("V100", "Volatility 100 Index", "SYNTHETIC"),
    ("STEP", "Step Index", "SYNTHETIC"),
    # Forex + gold
    ("EURUSD", "EURUSD", "FOREX"),
    ("GBPUSD", "GBPUSD", "FOREX"),
    ("USDJPY", "USDJPY", "FOREX"),
    ("AUDUSD", "AUDUSD", "FOREX"),
    ("XAUUSD", "XAUUSD", "FOREX"),
    # Crypto via Deriv's own CFDs (single-broker route, Section 3 open item)
    ("BTCUSD", "BTCUSD", "CRYPTO"),
    ("ETHUSD", "ETHUSD", "CRYPTO"),
]


def _load_symbols() -> list[SymbolSpec]:
    specs = [
        SymbolSpec(key, _env(f"QTX_BROKER_NAME_{key}", broker), asset,
                   _env(f"QTX_SOURCE_{key}", "mt5").lower())
        for key, broker, asset in _DEFAULT_SYMBOLS
    ]
    wanted = [s.strip().upper() for s in _env("QTX_SYMBOLS").split(",") if s.strip()]
    if wanted:
        unknown = set(wanted) - {s.key for s in specs}
        if unknown:
            raise ValueError(f"QTX_SYMBOLS has unknown keys: {sorted(unknown)}")
        specs = [s for s in specs if s.key in wanted]
    return specs


@dataclass(frozen=True)
class MT5Config:
    login: int | None
    password: str
    server: str
    terminal_path: str
    # IANA zone of the broker's server clock (e.g. "UTC", "Europe/Helsinki").
    # Empty = auto-detect a fixed offset from a 24/7 symbol's live tick.
    broker_tz: str
    # Fixed fallback offset in hours if auto-detection can't get a fresh tick.
    broker_utc_offset_hours: float | None


@dataclass(frozen=True)
class DBConfig:
    """Oracle connection (python-oracledb thin mode — no Oracle Client install needed)."""
    user: str
    password: str
    dsn: str               # host:port/service, e.g. localhost:1521/FREEPDB1
    wallet_dir: str = ""   # only for Oracle Cloud Autonomous DB with mTLS
    wallet_password: str = ""


@dataclass(frozen=True)
class NewsConfig:
    provider: str  # forexfactory | fmp | none
    fmp_api_key: str
    refresh_hours: int


@dataclass(frozen=True)
class FeedConfig:
    mt5: MT5Config
    db: DBConfig
    news: NewsConfig
    symbols: list[SymbolSpec] = field(default_factory=list)
    base_timeframe: str = "M15"
    backfill_days: int = 365
    # Seconds to wait after a bar closes before fetching it (lets the broker finalise it).
    bar_close_delay_s: int = 20
    preview_dir: Path = BASE_DIR / "preview_output"
    log_dir: Path = BASE_DIR / "logs"
    schema_file: Path = REPO_DIR / "sql" / "quantora_oracle_schema.sql"


def load_config() -> FeedConfig:
    login_raw = _env("MT5_LOGIN")
    offset_raw = _env("BROKER_UTC_OFFSET_HOURS")
    return FeedConfig(
        mt5=MT5Config(
            login=int(login_raw) if login_raw else None,
            password=_env("MT5_PASSWORD"),
            server=_env("MT5_SERVER"),
            terminal_path=_env("MT5_TERMINAL_PATH"),
            broker_tz=_env("BROKER_TZ"),
            broker_utc_offset_hours=float(offset_raw) if offset_raw else None,
        ),
        db=DBConfig(
            user=_env("ORACLE_USER", "quantora"),
            password=_env("ORACLE_PASSWORD"),
            dsn=_env("ORACLE_DSN", "localhost:1521/FREEPDB1"),
            wallet_dir=_env("ORACLE_WALLET_DIR"),
            wallet_password=_env("ORACLE_WALLET_PASSWORD"),
        ),
        news=NewsConfig(
            provider=_env("NEWS_PROVIDER", "forexfactory").lower(),
            fmp_api_key=_env("FMP_API_KEY"),
            refresh_hours=_env_int("NEWS_REFRESH_HOURS", 6),
        ),
        symbols=_load_symbols(),
        backfill_days=_env_int("BACKFILL_DAYS", 365),
        bar_close_delay_s=_env_int("BAR_CLOSE_DELAY_S", 20),
    )
