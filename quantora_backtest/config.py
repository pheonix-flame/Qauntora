"""Quantora Backtest — configuration.

Secrets (database password) come from the shared Qauntora/.env (or
quantora_backtest/.env, which wins). Trading assumptions come from
settings.toml in this folder. Nothing is shared in code with other engines.
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
class CostModel:
    spread: float
    slippage: float = 0.0
    swap_long_per_day: float = 0.0
    swap_short_per_day: float = 0.0


@dataclass(frozen=True)
class ModeProfile:
    name: str
    timeframe: str
    max_hold_minutes: int
    atr_period: int
    sl_atr: float
    tp_atr: float
    cooldown_bars_after_loss: int
    train_days: int
    test_days: int
    embargo_days: int

    @property
    def bar_minutes(self) -> int:
        return TIMEFRAME_MINUTES[self.timeframe]


@dataclass(frozen=True)
class GateConfig:
    min_trades: int = 30
    min_expectancy_r: float = 0.0
    max_drawdown_pct: float = 20.0
    min_random_percentile: float = 95.0
    risk_pct: float = 1.0


@dataclass(frozen=True)
class BacktestConfig:
    db: DBConfig
    modes: dict[str, ModeProfile]
    costs: dict[str, CostModel]
    gate: GateConfig
    random_runs: int = 200
    seed: int = 42
    output_dir: Path = BASE_DIR / "backtest_output"
    settings_file: Path = BASE_DIR / "settings.toml"

    def cost_for(self, symbol: str) -> CostModel:
        try:
            return self.costs[symbol]
        except KeyError:
            raise MissingCostError(symbol, self.settings_file) from None


class MissingCostError(LookupError):
    def __init__(self, symbol: str, settings_file: Path) -> None:
        super().__init__(
            f"No trading costs for {symbol} in {settings_file}. A cost-free backtest overstates results, "
            f"so it won't run. Run `python main.py check` in quantora_feed and paste the {symbol} line "
            f"under [costs].")
        self.symbol = symbol


def parse_settings(raw: dict, settings_file: Path) -> dict:
    modes = {}
    for name in MODES:
        m = raw.get("modes", {}).get(name)
        if m is None:
            continue
        if m["timeframe"] not in TIMEFRAME_MINUTES:
            raise ValueError(f"[modes.{name}] timeframe must be one of {list(TIMEFRAME_MINUTES)}")
        modes[name] = ModeProfile(name=name, **m)
    costs = {}
    for sym, c in raw.get("costs", {}).items():
        cm = CostModel(**c)
        if cm.spread < 0 or cm.slippage < 0:
            raise ValueError(f"[costs] {sym}: spread/slippage can't be negative")
        costs[sym.upper()] = cm
    base = raw.get("baselines", {})
    return dict(modes=modes, costs=costs, gate=GateConfig(**raw.get("gate", {})),
                random_runs=int(base.get("random_runs", 200)), seed=int(base.get("seed", 42)),
                settings_file=settings_file)


def load_config(settings_file: Path | None = None) -> BacktestConfig:
    path = settings_file or BASE_DIR / "settings.toml"
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    return BacktestConfig(
        db=DBConfig(user=_env("ORACLE_USER", "quantora"), password=_env("ORACLE_PASSWORD"),
                    dsn=_env("ORACLE_DSN", "localhost:1521/FREEPDB1"),
                    wallet_dir=_env("ORACLE_WALLET_DIR"), wallet_password=_env("ORACLE_WALLET_PASSWORD")),
        **parse_settings(raw, path),
        output_dir=Path(_env("BACKTEST_OUTPUT_DIR")) if _env("BACKTEST_OUTPUT_DIR") else BASE_DIR / "backtest_output",
    )
