"""Quantora Feed — MT5 price source (Deriv).

Pulls closed M15 bars from a running, logged-in MT5 terminal and returns them
as DataFrames shaped exactly like `qtx_ohlcv`, with timestamps normalised to
true UTC (spec Section 12, "Timezone normalization").

Notes
-----
* The `MetaTrader5` package is Windows-only and talks to a desktop terminal
  that must be running and logged in. Import is lazy so tests and `--help`
  work anywhere.
* MT5 bar times are the broker's *server wall clock* encoded as if it were
  UTC. We either convert with an explicit IANA zone (BROKER_TZ — DST-correct
  for history) or apply a fixed offset auto-detected from a 24/7 symbol's
  live tick. The chosen conversion is always logged, never assumed.
* MT5 bars are built from BID prices, so `price_side` is written as 'BID'.
* Volume is MT5 `tick_volume` (CFDs on Deriv report real_volume = 0).
* Single-owner rule (Section 10): until Quantora Sentinel exists, Feed is the
  only process using MT5. Once Sentinel owns the live connection, Feed's live
  loop must request data through it and only open its own session for backfill.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from types import ModuleType
from typing import Callable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from config import FeedConfig, MT5Config, SymbolSpec
from models import BAR_MINUTES, empty_ohlcv, naive_utc
from price_sources.base import PriceSource

log = logging.getLogger("quantora.feed")

_CHUNK = timedelta(days=30)
# A tick only proves the server clock if it's live: watch it move for up to this long.
TICK_WATCH_S = 5.0
TICK_POLL_S = 0.5


def _tick_ms(tick) -> int:
    return int(getattr(tick, "time_msc", 0) or int(tick.time) * 1000)


def _import_mt5() -> ModuleType:
    try:
        import MetaTrader5 as mt5  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "The MetaTrader5 package is not installed. It only runs on Windows "
            "(pip install MetaTrader5) alongside a logged-in MT5 terminal."
        ) from exc
    return mt5


def server_times_to_utc(epochs: np.ndarray, broker_tz: str, fixed_offset_s: int) -> pd.DatetimeIndex:
    """Convert MT5 server-clock epoch seconds to naive UTC timestamps.

    If `broker_tz` is set, each bar is localised in that zone (handles DST
    transitions inside historical data). Otherwise `fixed_offset_s` is
    subtracted from every bar.
    """
    wall = pd.to_datetime(np.asarray(epochs, dtype="int64"), unit="s")  # naive server wall clock
    if broker_tz:
        tz = ZoneInfo(broker_tz)
        try:
            aware = wall.tz_localize(tz, ambiguous="infer", nonexistent="shift_forward")
        except Exception:  # noqa: BLE001 — repeated hour can't always be inferred
            log.warning("Ambiguous DST hour in %s data; treating repeated hour as standard time", broker_tz)
            aware = wall.tz_localize(tz, ambiguous=False, nonexistent="shift_forward")
        return aware.tz_convert("UTC").tz_localize(None)
    return wall - pd.Timedelta(seconds=fixed_offset_s)


def rates_to_frame(
    rates: np.ndarray | None,
    spec: SymbolSpec,
    timeframe: str,
    broker_tz: str,
    fixed_offset_s: int,
    start_utc: datetime,
    now_utc: datetime,
) -> pd.DataFrame:
    """Shape raw MT5 rates into qtx_ohlcv rows: UTC, closed bars only, >= start."""
    if rates is None or len(rates) == 0:
        return empty_ohlcv()
    raw = pd.DataFrame(rates)
    bar_ts = server_times_to_utc(raw["time"].to_numpy(), broker_tz, fixed_offset_s)
    df = pd.DataFrame({
        "symbol": spec.key,
        "asset_class": spec.asset_class,
        "timeframe": timeframe,
        "bar_ts": bar_ts,
        "open_price": raw["open"].astype(float).to_numpy(),
        "high_price": raw["high"].astype(float).to_numpy(),
        "low_price": raw["low"].astype(float).to_numpy(),
        "close_price": raw["close"].astype(float).to_numpy(),
        "volume": raw["tick_volume"].astype(float).to_numpy(),
        "price_side": "BID",
    })
    bar_len = pd.Timedelta(minutes=BAR_MINUTES[timeframe])
    start, now = naive_utc(start_utc), naive_utc(now_utc)
    closed = df["bar_ts"] + bar_len <= now  # never store the still-forming candle
    df = df[closed & (df["bar_ts"] >= start)]
    return df.drop_duplicates(subset=["symbol", "timeframe", "bar_ts"]).sort_values("bar_ts").reset_index(drop=True)


class MT5Source(PriceSource):
    name = "mt5"

    def __init__(
        self,
        cfg: MT5Config,
        mt5_module: ModuleType | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.cfg = cfg
        self._mt5 = mt5_module
        self._clock = clock
        self._sleep = sleep
        self.fixed_offset_s = 0
        self.connected = False

    @property
    def mt5(self) -> ModuleType:
        if self._mt5 is None:
            self._mt5 = _import_mt5()
        return self._mt5

    def now_utc(self) -> datetime:
        return datetime.fromtimestamp(self._clock(), tz=timezone.utc)

    @classmethod
    def from_config(cls, cfg: FeedConfig) -> "MT5Source":
        return cls(cfg.mt5)

    def describe_clock(self) -> str:
        if self.cfg.broker_tz:
            return f"zone {self.cfg.broker_tz}"
        return f"UTC{self.fixed_offset_s / 3600:+.2f}h (auto-detected)"

    def utc_to_broker_clock(self, ts: pd.Timestamp) -> pd.Timestamp:
        if self.cfg.broker_tz:
            return pd.Timestamp(ts).tz_localize("UTC").tz_convert(self.cfg.broker_tz).tz_localize(None)
        return pd.Timestamp(ts) + pd.Timedelta(seconds=self.fixed_offset_s)

    def _live_tick_time(self, sym: str) -> int | None:
        """Server-clock time of a tick seen to *arrive* while we watch, else None.

        A single tick can be stale by exactly N x 15 min (market paused), which would
        look like a clean but wrong offset — so we require the tick to advance.
        """
        first = self.mt5.symbol_info_tick(sym)
        if first is None or not first.time:
            return None
        waited = 0.0
        while waited < TICK_WATCH_S:
            self._sleep(TICK_POLL_S)
            waited += TICK_POLL_S or TICK_WATCH_S  # TICK_POLL_S=0 in tests: one re-read
            tick = self.mt5.symbol_info_tick(sym)
            if tick is not None and _tick_ms(tick) > _tick_ms(first):
                return int(tick.time)
        return None

    def verify_clock(self, symbols: list[SymbolSpec]) -> tuple[bool | None, str]:
        """Compare the conversion Feed is using with a live measurement of the server clock."""
        order = {"SYNTHETIC": 0, "CRYPTO": 1, "FOREX": 2}
        reference = [s.broker_symbol for s in sorted(symbols, key=lambda s: order.get(s.asset_class, 9))]
        measured = self.detect_fixed_offset_s(reference, allow_fallback=False)
        if measured is None:
            return None, "no fresh tick to measure the server clock (markets closed?)"
        if self.cfg.broker_tz:
            now = datetime.fromtimestamp(self._clock(), tz=ZoneInfo(self.cfg.broker_tz))
            used = int(now.utcoffset().total_seconds())
            source = f"BROKER_TZ={self.cfg.broker_tz}"
        else:
            used, source = self.fixed_offset_s, "auto-detected offset"
        if used != measured:
            return False, (f"{source} means UTC{used / 3600:+.2f}h right now, but the live tick says the "
                           f"server clock is UTC{measured / 3600:+.2f}h — every stored timestamp is off by "
                           f"{abs(used - measured) / 3600:.2f} h. Fix .env and re-backfill.")
        return True, f"{source} (UTC{used / 3600:+.2f}h) agrees with the live server clock"

    # -- connection ---------------------------------------------------------
    def connect(self, symbols: list[SymbolSpec]) -> None:
        # 24/7 instruments first — they tick constantly, so their tick time reveals the server clock.
        order = {"SYNTHETIC": 0, "CRYPTO": 1, "FOREX": 2}
        reference = [s.broker_symbol for s in sorted(symbols, key=lambda s: order.get(s.asset_class, 9))]
        mt5 = self.mt5
        kwargs: dict = {"timeout": 60_000}
        if self.cfg.login:
            kwargs.update(login=self.cfg.login, password=self.cfg.password, server=self.cfg.server)
        ok = mt5.initialize(self.cfg.terminal_path, **kwargs) if self.cfg.terminal_path else mt5.initialize(**kwargs)
        if not ok:
            err = mt5.last_error()
            mt5.shutdown()
            raise ConnectionError(f"mt5.initialize() failed: {err}")
        acct = mt5.account_info()
        if acct is None:
            err = mt5.last_error()
            mt5.shutdown()
            raise ConnectionError(f"MT5 connected but no account is logged in: {err}")
        mode = {0: "DEMO", 1: "CONTEST", 2: "REAL"}.get(getattr(acct, "trade_mode", -1), "UNKNOWN")
        log.info("MT5 connected: login=%s server=%s mode=%s currency=%s balance=%.2f leverage=1:%s",
                 acct.login, acct.server, mode, acct.currency, acct.balance, acct.leverage)
        self.connected = True
        self._resolve_time_conversion(reference)

    def shutdown(self) -> None:
        if self.connected:
            self.mt5.shutdown()
            self.connected = False

    def _resolve_time_conversion(self, reference_symbols: list[str]) -> None:
        if self.cfg.broker_tz:
            ZoneInfo(self.cfg.broker_tz)  # fail fast on a typo
            log.info("Timestamp normalisation: broker clock treated as IANA zone %s (DST-aware)", self.cfg.broker_tz)
            return
        self.fixed_offset_s = self.detect_fixed_offset_s(reference_symbols)
        hours = self.fixed_offset_s / 3600
        log.info("Timestamp normalisation: fixed broker offset UTC%+.2fh (auto-detected) subtracted from all bars", hours)
        if self.fixed_offset_s != 0:
            log.warning("Broker clock is not UTC. A fixed offset is wrong across DST changes in history — "
                        "set BROKER_TZ in .env if your broker observes DST.")

    def detect_fixed_offset_s(self, reference_symbols: list[str], allow_fallback: bool = True) -> int | None:
        """Server-clock minus true UTC, from a fresh tick on a 24/7 symbol, rounded to 15 min.

        With allow_fallback=False, returns None instead of using BROKER_UTC_OFFSET_HOURS
        when no fresh tick is available (so `verify` gets an independent measurement).
        """
        for sym in reference_symbols:
            if not self.ensure_symbol(sym):
                continue
            tick_time = self._live_tick_time(sym)
            if tick_time is None:
                log.debug("No live ticks on %s; trying next symbol", sym)
                continue
            diff = tick_time - self._clock()
            rounded = int(round(diff / 900.0) * 900)
            if abs(diff - rounded) <= 120:
                log.debug("Offset from %s tick: raw diff %.0fs -> %ds", sym, diff, rounded)
                return rounded
            log.debug("Tick on %s looks stale (diff %.0fs); trying next symbol", sym, diff)
        if not allow_fallback:
            return None
        if self.cfg.broker_utc_offset_hours is not None:
            log.warning("No fresh tick for offset detection; using BROKER_UTC_OFFSET_HOURS=%s",
                        self.cfg.broker_utc_offset_hours)
            return int(self.cfg.broker_utc_offset_hours * 3600)
        raise RuntimeError("Could not detect the broker's UTC offset (no fresh ticks). "
                           "Set BROKER_TZ or BROKER_UTC_OFFSET_HOURS in .env.")

    # -- symbols ------------------------------------------------------------
    def ensure_symbol(self, broker_symbol: str) -> bool:
        info = self.mt5.symbol_info(broker_symbol)
        if info is None:
            return False
        if not info.visible and not self.mt5.symbol_select(broker_symbol, True):
            log.warning("Could not add %s to Market Watch: %s", broker_symbol, self.mt5.last_error())
            return False
        return True

    def symbol_specs(self, spec: SymbolSpec) -> dict | None:
        info = self.mt5.symbol_info(spec.broker_symbol)
        if info is None:
            return None
        return {
            "digits": info.digits,
            "point": info.point,
            "volume_min": info.volume_min,
            "volume_step": info.volume_step,
            "volume_max": info.volume_max,
            "contract_size": info.trade_contract_size,
            "tick_size": info.trade_tick_size,
            "tick_value": info.trade_tick_value,
            "spread_points": info.spread,
        }

    def suggest_names(self, pattern: str) -> list[str]:
        found = self.mt5.symbols_get(group=f"*{pattern}*") or []
        return [s.name for s in found][:10]

    # -- bars ---------------------------------------------------------------
    def fetch_bars(self, spec: SymbolSpec, start_utc: datetime, timeframe: str = "M15") -> pd.DataFrame:
        """All closed bars from `start_utc` (inclusive) to now, in UTC."""
        mt5 = self.mt5
        if not self.ensure_symbol(spec.broker_symbol):
            raise LookupError(f"Symbol '{spec.broker_symbol}' not found on this MT5 account")
        tf_const = getattr(mt5, f"TIMEFRAME_{timeframe}")
        now = self.now_utc()
        # Query in server-clock terms with a 1-day margin either side, then filter
        # precisely after conversion — avoids off-by-offset gaps at the edges.
        cursor = naive_utc(start_utc).to_pydatetime().replace(tzinfo=timezone.utc) - timedelta(days=1)
        end = now + timedelta(days=1)
        frames = []
        while cursor < end:
            chunk_end = min(cursor + _CHUNK, end)
            rates = mt5.copy_rates_range(spec.broker_symbol, tf_const, cursor, chunk_end)
            if rates is None:
                log.warning("copy_rates_range(%s) returned None: %s", spec.broker_symbol, mt5.last_error())
            elif len(rates):
                frames.append(rates_to_frame(rates, spec, timeframe, self.cfg.broker_tz,
                                             self.fixed_offset_s, start_utc, now))
            cursor = chunk_end
        if not frames:
            return empty_ohlcv()
        out = pd.concat(frames, ignore_index=True)
        return out.drop_duplicates(subset=["symbol", "timeframe", "bar_ts"]).sort_values("bar_ts").reset_index(drop=True)
