"""Channel factory. Adding e.g. email or WhatsApp later = one new module + one branch here."""
from __future__ import annotations

from channels.base import Channel, DeliveryError
from config import NotifyConfig


def build_channel(cfg: NotifyConfig, name: str = "telegram") -> Channel:
    if name == "console":
        from channels.console import ConsoleChannel
        return ConsoleChannel()
    if name == "telegram":
        from channels.telegram import TelegramChannel
        return TelegramChannel(cfg.telegram)
    raise ValueError(f"Unknown channel '{name}' (telegram | console)")


__all__ = ["Channel", "DeliveryError", "build_channel"]
