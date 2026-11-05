"""Where messages go. Every channel takes the same HTML-formatted text."""
from __future__ import annotations

from abc import ABC, abstractmethod


class DeliveryError(RuntimeError):
    """Raised when a message definitely wasn't delivered (Notify will retry it next cycle)."""


class Channel(ABC):
    name: str = ""

    @abstractmethod
    def send(self, html: str) -> None:
        """Deliver one message or raise DeliveryError."""
