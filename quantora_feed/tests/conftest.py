"""Test helpers: put quantora_feed/ on sys.path and provide a fake MT5 terminal."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

RATE_DTYPE = [("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"), ("close", "<f8"),
              ("tick_volume", "<u8"), ("spread", "<i4"), ("real_volume", "<u8")]


class FakeMT5:
    """Mimics the parts of the MetaTrader5 module Feed uses.

    `offset_s` is the broker server clock minus UTC. Bars are generated every
    15 minutes in server-clock epoch seconds, like the real terminal returns.
    """
    TIMEFRAME_M15 = 15
    TIMEFRAME_H1 = 60

    def __init__(self, clock, offset_s=0, symbols=("Volatility 75 Index", "EURUSD"), tick_lag_s=1, live=True):
        self.clock = clock
        self.offset_s = offset_s
        self.symbols = {name: SimpleNamespace(name=name, visible=False, digits=2, point=0.01,
                                              volume_min=0.001, volume_step=0.001, volume_max=100.0,
                                              trade_contract_size=1.0, trade_tick_size=0.01,
                                              trade_tick_value=0.01, spread=500)
                        for name in symbols}
        self.tick_lag_s = tick_lag_s
        self.live = live          # live: every read sees a newer tick; else the tick is frozen
        self._tick_reads = 0
        self.range_calls = []
        self.initialized = False

    def initialize(self, *args, **kwargs):
        self.initialized = True
        return True

    def shutdown(self):
        self.initialized = False

    def last_error(self):
        return (1, "Success")

    def account_info(self):
        return SimpleNamespace(login=123, server="Deriv-Demo", trade_mode=0, currency="USD",
                               balance=100.0, leverage=1000)

    def symbol_info(self, name):
        return self.symbols.get(name)

    def symbol_select(self, name, enable):
        self.symbols[name].visible = True
        return True

    def symbols_get(self, group="*"):
        needle = group.strip("*").lower()
        return [s for n, s in self.symbols.items() if needle in n.lower()]

    def symbol_info_tick(self, name):
        if name not in self.symbols:
            return None
        self._tick_reads += 1
        t = int(self.clock() + self.offset_s - self.tick_lag_s)
        ms = t * 1000 + (self._tick_reads if self.live else 0)
        return SimpleNamespace(time=t, time_msc=ms)

    @staticmethod
    def m15_prices(times: np.ndarray):
        """Deterministic, random-looking M15 prices for server-clock bar starts."""
        n = times // 900
        noise = np.modf(np.abs(np.sin(n * 12.9898) * 43758.5453))[0]
        o = 100 + 5 * noise
        c = o + 0.5
        return o, o + 1, o - 1, c

    def copy_rates_range(self, name, timeframe, date_from, date_to):
        self.range_calls.append((name, timeframe, date_from, date_to))
        step = timeframe * 60
        server_now = self.clock() + self.offset_s
        lo = int(date_from.timestamp()) // step * step
        hi = min(int(date_to.timestamp()), int(server_now))
        times = np.arange(lo, hi + 1, step, dtype="int64")
        times = times[(times >= date_from.timestamp()) & (times <= date_to.timestamp())]
        out = np.zeros(len(times), dtype=RATE_DTYPE)
        out["time"] = times
        if timeframe == 15:
            o, h, l, c = self.m15_prices(times)
            vol = 10
        else:  # H1 built from its four M15 bars, exactly like the real terminal
            quarters = [self.m15_prices(times + q * 900) for q in range(4)]
            o = quarters[0][0]
            h = np.max([q[1] for q in quarters], axis=0)
            l = np.min([q[2] for q in quarters], axis=0)
            c = quarters[3][3]
            vol = 40
        out["open"], out["high"], out["low"], out["close"] = o, h, l, c
        out["tick_volume"] = vol
        return out


class FrozenClock:
    def __init__(self, dt: datetime):
        self.t = dt.replace(tzinfo=timezone.utc).timestamp()

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture(autouse=True)
def no_tick_wait(monkeypatch):
    """Don't really sleep while watching for live ticks."""
    import price_sources.mt5 as mt5_mod
    monkeypatch.setattr(mt5_mod, "TICK_POLL_S", 0)


@pytest.fixture
def clock():
    return FrozenClock(datetime(2026, 11, 3, 12, 7, 30))
