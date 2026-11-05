"""Print messages instead of sending them (--channel console): for trying Notify before the bot exists."""
from __future__ import annotations

import html
import re

from channels.base import Channel


class ConsoleChannel(Channel):
    name = "console"

    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, text: str) -> None:
        self.sent.append(text)
        plain = html.unescape(re.sub(r"</?[a-z]+>", "", text))
        print("─" * 60 + "\n" + plain)
