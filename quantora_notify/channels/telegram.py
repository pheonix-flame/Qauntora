"""Telegram Bot API channel (https://core.telegram.org/bots/api).

The bot token is part of every request URL, and Python's HTTP errors include
the URL — so every error message is scrubbed of the token before it can reach
a log or the screen.
"""
from __future__ import annotations

import time

import requests

from channels.base import Channel, DeliveryError
from config import TelegramConfig

MAX_LEN = 4096  # Telegram's limit per message


class TelegramChannel(Channel):
    name = "telegram"

    def __init__(self, cfg: TelegramConfig, session: requests.Session | None = None, sleep=time.sleep) -> None:
        if not cfg.token:
            raise ValueError("TELEGRAM_BOT_TOKEN is empty — set it in Qauntora/.env (README: 'Create your Telegram bot')")
        self.cfg = cfg
        self.http = session or requests.Session()
        self._sleep = sleep

    def _scrub(self, text: str) -> str:
        return str(text).replace(self.cfg.token, "<token>")

    def _call(self, method: str, payload: dict | None = None, attempts: int = 3) -> dict:
        url = f"{self.cfg.api_base}/bot{self.cfg.token}/{method}"
        last = ""
        for attempt in range(1, attempts + 1):
            try:
                resp = self.http.post(url, json=payload or {}, timeout=20)
            except requests.RequestException as exc:
                last = self._scrub(exc)
                self._sleep(2 * attempt)
                continue
            try:
                body = resp.json()
            except ValueError:
                body = {"ok": False, "description": resp.text[:200]}
            if body.get("ok"):
                return body.get("result", {})
            if resp.status_code == 429:  # flood control: Telegram says how long to wait
                wait = int(body.get("parameters", {}).get("retry_after", 5))
                self._sleep(min(wait, 60))
                last = f"rate limited ({wait}s)"
                continue
            desc = self._scrub(body.get("description", f"HTTP {resp.status_code}"))
            if resp.status_code in (400, 401, 403, 404):  # won't fix itself by retrying
                raise DeliveryError(f"Telegram refused {method}: {desc}{_hint(resp.status_code, desc)}")
            last = desc
            self._sleep(2 * attempt)
        raise DeliveryError(f"Telegram {method} failed after {attempts} attempts: {last}")

    def send(self, html: str) -> None:
        if not self.cfg.chat_id:
            raise DeliveryError("TELEGRAM_CHAT_ID is empty — run `python main.py chat-id` to find it")
        for part in _split(html):
            self._call("sendMessage", {"chat_id": self.cfg.chat_id, "text": part, "parse_mode": "HTML",
                                       "disable_web_page_preview": True})

    def get_me(self) -> dict:
        return self._call("getMe")

    def recent_chats(self) -> list[dict]:
        """Chats that recently messaged the bot: [{id, type, name}] — for finding TELEGRAM_CHAT_ID."""
        seen = {}
        for upd in self._call("getUpdates", {"timeout": 0}):
            msg = upd.get("message") or upd.get("channel_post") or upd.get("my_chat_member") or {}
            chat = msg.get("chat") or {}
            if "id" in chat:
                name = chat.get("title") or " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x)
                seen[chat["id"]] = {"id": chat["id"], "type": chat.get("type", "?"), "name": name or "?"}
        return list(seen.values())


def _hint(status: int, desc: str) -> str:
    d = desc.lower()
    if status == 401:
        return " — the bot token is wrong; copy it again from @BotFather"
    if "chat not found" in d:
        return " — wrong TELEGRAM_CHAT_ID, or you haven't pressed Start in your bot's chat yet"
    if status == 403:
        return " — the bot was blocked or removed; open its chat and press Start again"
    return ""


def _split(text: str) -> list[str]:
    if len(text) <= MAX_LEN:
        return [text]
    parts, cur = [], ""
    for line in text.splitlines(keepends=True):
        if len(cur) + len(line) > MAX_LEN:
            parts.append(cur)
            cur = ""
        cur += line[:MAX_LEN]
    return parts + ([cur] if cur else [])
