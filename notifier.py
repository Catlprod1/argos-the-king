from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

from .config import Config


class NotificationError(RuntimeError):
    pass


@dataclass(frozen=True)
class NotificationResult:
    channel: str
    delivered: bool
    detail: str


class ConsoleNotifier:
    def send(self, text: str) -> NotificationResult:
        print(text)
        return NotificationResult("console", True, "printed locally")


class TelegramNotifier:
    """Owner-only Telegram notifications using secrets supplied by environment."""

    def __init__(self, token: str, chat_id: str):
        if not token or not chat_id:
            raise NotificationError("Telegram token and owner chat ID are required")
        self._token = token
        self._chat_id = chat_id

    @classmethod
    def from_config(cls, config: Config) -> "TelegramNotifier":
        telegram = config.section("communications").get("telegram", {})
        token_env = telegram.get("bot_token_env", "ARGOS_TELEGRAM_BOT_TOKEN")
        chat_env = telegram.get("chat_id_env", "ARGOS_TELEGRAM_CHAT_ID")
        return cls(os.environ.get(token_env, ""), os.environ.get(chat_env, ""))

    def send(self, text: str) -> NotificationResult:
        endpoint = f"https://api.telegram.org/bot{self._token}/sendMessage"
        payload = json.dumps({
            "chat_id": self._chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }).encode("utf-8")
        request = urllib.request.Request(
            endpoint,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise NotificationError(f"Telegram delivery failed: {exc}") from exc
        if not body.get("ok"):
            raise NotificationError("Telegram rejected the notification")
        return NotificationResult("telegram", True, "delivered to owner")


def owner_notifier(config: Config):
    communications = config.section("communications")
    telegram = communications.get("telegram", {})
    if communications.get("morning_brief_channel") == "telegram" and telegram.get("enabled", False):
        return TelegramNotifier.from_config(config)
    return ConsoleNotifier()
