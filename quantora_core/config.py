"""Quantora Core — configuration.

Secrets come from the shared Qauntora/.env (quantora_core/.env wins if present).
Signal logic settings come from settings.toml here. A confluence config
approved in qtx_config_versions overrides the [defaults] section.
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

TIMEFRAME_MINUTES = {"M15": 15, "H1": 60, "H4": 240, "D1": 1440, "W1": 10080}
MODES = ("SCALP", "SWING", "LONG")
WEIGHT_KEYS = ("trend", "strength", "momentum", "location", "volume")
PARAM_TYPES = {
    "ema_fast": int, "ema_slow": int, "adx_period": int, "adx_min": float, "momentum": str,
    "rsi_period": int, "rsi_extreme": float, "macd_fast": int, "macd_slow": int, "macd_signal": int,
    "bb_period": int, "bb_std": float, "obv_slope_bars": int, "chase_atr": float,
}


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
class ModeProfile:
    name: str
    timeframe: str
    lookback_bars: int
    atr_period: int
    sl_atr: float
    tp_atr: float

    @property
    def bar_minutes(self) -> int:
        return TIMEFRAME_MINUTES[self.timeframe]


@dataclass(frozen=True)
class SignalConfig:
    """One confluence configuration: thresholds, weights, indicator parameters."""
    tier2_threshold: float
    tier3_threshold: float
    weights: dict
    params: dict
    version_id: int | None = None  # None = settings.toml defaults (not an approved DB config)

    @property
    def source(self) -> str:
        return f"approved config v{self.version_id}" if self.version_id else "settings.toml defaults"


def validate_signal_config(tier2: float, tier3: float, weights: dict, params: dict,
                           version_id: int | None = None, base: SignalConfig | None = None) -> SignalConfig:
    unknown_w = set(weights) - set(WEIGHT_KEYS)
    unknown_p = set(params) - set(PARAM_TYPES)
    if unknown_w or unknown_p:
        raise ValueError(f"Unknown config keys: weights {sorted(unknown_w)}, params {sorted(unknown_p)}")
    merged_w = {**(base.weights if base else {}), **weights}
    merged_p = {**(base.params if base else {}), **params}
    merged_p = {k: PARAM_TYPES[k](v) for k, v in merged_p.items()}
    if not 0 < tier2 <= tier3 <= 1:
        raise ValueError(f"Need 0 < tier2_threshold <= tier3_threshold <= 1 (got {tier2}, {tier3})")
    if any(w < 0 for w in merged_w.values()):
        raise ValueError("Weights can't be negative")
    if merged_p.get("momentum", "RSI").upper() not in ("RSI", "MACD"):
        raise ValueError("params.momentum must be RSI or MACD")
    if merged_p.get("ema_fast", 1) >= merged_p.get("ema_slow", 2):
        raise ValueError("ema_fast must be smaller than ema_slow")
    return SignalConfig(float(tier2), float(tier3), merged_w, merged_p, version_id)


@dataclass(frozen=True)
class CoreConfig:
    db: DBConfig
    universe: dict[str, list[str]]
    asset_class: dict[str, str]
    modes: dict[str, ModeProfile]
    defaults: SignalConfig
    news_currencies: dict[str, list[str]]
    blackout_before_minutes: int = 30
    blackout_after_minutes: int = 15
    seconds_after_bar_close: int = 60
    max_feed_lag_minutes: int = 45
    max_catch_up_bars: int = 50
    output_dir: Path = BASE_DIR / "core_output"
    settings_file: Path = BASE_DIR / "settings.toml"

    def pairs(self) -> list[tuple[str, str]]:
        return [(sym, mode) for sym, modes in self.universe.items() for mode in modes]

    def asset(self, symbol: str) -> str:
        return self.asset_class.get(symbol, "SYNTHETIC")


def parse_settings(raw: dict, settings_file: Path) -> dict:
    modes = {}
    for name in MODES:
        m = raw.get("modes", {}).get(name)
        if m is None:
            continue
        if m["timeframe"] not in TIMEFRAME_MINUTES:
            raise ValueError(f"[modes.{name}] timeframe must be one of {list(TIMEFRAME_MINUTES)}")
        modes[name] = ModeProfile(name=name, **m)
    universe = {sym.upper(): [m.upper() for m in ms] for sym, ms in raw.get("universe", {}).items()}
    for sym, ms in universe.items():
        bad = [m for m in ms if m not in modes]
        if bad:
            raise ValueError(f"[universe] {sym}: unknown mode(s) {bad} — define them under [modes]")
    d = raw.get("defaults", {})
    defaults = validate_signal_config(d.get("tier2_threshold", 0.6), d.get("tier3_threshold", 0.8),
                                      d.get("weights", {}), d.get("params", {}))
    news = raw.get("news", {})
    run = raw.get("run", {})
    return dict(
        universe=universe,
        asset_class={k.upper(): v.upper() for k, v in raw.get("asset_class", {}).items()},
        modes=modes, defaults=defaults,
        news_currencies={k.upper(): [c.upper() for c in v] for k, v in news.get("currencies", {}).items()},
        blackout_before_minutes=int(news.get("blackout_before_minutes", 30)),
        blackout_after_minutes=int(news.get("blackout_after_minutes", 15)),
        seconds_after_bar_close=int(run.get("seconds_after_bar_close", 60)),
        max_feed_lag_minutes=int(run.get("max_feed_lag_minutes", 45)),
        max_catch_up_bars=int(run.get("max_catch_up_bars", 50)),
        settings_file=settings_file,
    )


def load_config(settings_file: Path | None = None) -> CoreConfig:
    path = settings_file or BASE_DIR / "settings.toml"
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    return CoreConfig(
        db=DBConfig(user=_env("ORACLE_USER", "quantora"), password=_env("ORACLE_PASSWORD"),
                    dsn=_env("ORACLE_DSN", "localhost:1521/FREEPDB1"),
                    wallet_dir=_env("ORACLE_WALLET_DIR"), wallet_password=_env("ORACLE_WALLET_PASSWORD")),
        **parse_settings(raw, path),
        output_dir=Path(_env("CORE_OUTPUT_DIR")) if _env("CORE_OUTPUT_DIR") else BASE_DIR / "core_output",
    )
