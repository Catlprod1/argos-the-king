"""Owner-only Telegram commands and image conversation; no remote execution."""

from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from .approvals import ApprovalQueue
from .audit import AuditLog
from .briefing import BriefingService
from .chat import ChatUnavailable, MAX_IMAGE_BYTES, OwnerChat
from .config import Config
from .notifier import TelegramNotifier
from .market_analysis import market_report
from .email_analysis import GmailMetadataReader
from .bybit_account import testnet_balance
from .bybit_subaccount import SubaccountError, SubaccountReader
from .live_review import ReviewError, chat_bundle, review
from .market_assessment import assess
from .runtime_watch import timer_state, watch_context
from .strategy_query import strategy_query
from .whalebot import digest
from .service import ArgosService
from .training import TrainingRegister
from . import __version__


class TelegramCommands:
    def __init__(self, config: Config):
        settings = config.section("communications").get("telegram", {})
        if not settings.get("enabled", False):
            raise ValueError("Telegram is disabled")
        token = os.environ.get(settings.get("bot_token_env", "ARGOS_TELEGRAM_BOT_TOKEN"), "")
        owner = os.environ.get(settings.get("chat_id_env", "ARGOS_TELEGRAM_CHAT_ID"), "")
        if not token or not re.fullmatch(r"[0-9]+", owner):
            raise ValueError("Telegram bot token and numeric owner chat ID are required")
        self.config = config
        self.token = token
        self.owner_chat_id = int(owner)
        self.sender = TelegramNotifier(token, owner)
        self.offset_path = config.data_directory / "telegram-offset.json"
        self.chat = OwnerChat(config.data_directory,
                             context_provider=lambda message: chat_bundle(
                                 message, self.config.section("trading").get("market_universe", []),
                                 config.data_directory),
                             runtime_provider=lambda: watch_context(
                                 self.config.section("trading").get("market_universe", [])))

    def _fetch(self, offset: int) -> list[dict]:
        # Never print a URL or response body: the API URL contains the bot token.
        endpoint = f"https://api.telegram.org/bot{self.token}/getUpdates"
        payload = json.dumps({"offset": offset, "limit": 100, "timeout": 0,
                              "allowed_updates": ["message"]}).encode("utf-8")
        req = urllib.request.Request(endpoint, payload,
                                     {"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Telegram poll HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, ValueError):
            raise RuntimeError("Telegram poll failed") from None
        if not result.get("ok") or not isinstance(result.get("result"), list):
            raise RuntimeError("Telegram poll rejected")
        return result["result"]

    def _image(self, message: dict) -> bytes:
        photos = message.get("photo")
        document = message.get("document")
        if isinstance(photos, list) and photos:
            item = photos[-1]
        elif isinstance(document, dict) and document.get("mime_type") in (
                "image/jpeg", "image/png", "image/webp"):
            item = document
        else:
            raise ValueError("Send a photo or a JPEG, PNG, or WebP image file.")
        if not isinstance(item, dict) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", str(item.get("file_id", ""))):
            raise ValueError("Telegram supplied an invalid image ID.")
        if type(item.get("file_size")) is int and item["file_size"] > MAX_IMAGE_BYTES:
            raise ValueError("Please send an image under 3 MB.")
        request = urllib.request.Request(f"https://api.telegram.org/bot{self.token}/getFile",
            json.dumps({"file_id": item["file_id"]}).encode("utf-8"),
            {"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                result = json.load(response)
            path = result["result"]["file_path"]
            if not result.get("ok") or not isinstance(path, str) or not re.fullmatch(r"[A-Za-z0-9_./-]{1,300}", path) or path.startswith("/") or ".." in path:
                raise ValueError("Invalid Telegram file path")
            download = urllib.request.Request(f"https://api.telegram.org/file/bot{self.token}/{path}")
            with urllib.request.urlopen(download, timeout=15) as response:
                image = response.read(MAX_IMAGE_BYTES + 1)
        except (OSError, ValueError, KeyError, TypeError):
            # Telegram URLs contain the bot token; never include the underlying exception.
            raise ValueError("Could not read that image from Telegram.") from None
        if len(image) > MAX_IMAGE_BYTES:
            raise ValueError("Please send an image under 3 MB.")
        return image

    def _offset(self) -> int:
        try:
            value = json.loads(self.offset_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return 0
        offset = value.get("offset")
        if type(offset) is not int or offset < 0:
            raise ValueError("Invalid Telegram offset file")
        return offset

    def _save_offset(self, offset: int) -> None:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix="telegram-offset-",
                                             dir=self.offset_path.parent, delete=False) as output:
                temporary = Path(output.name)
                os.chmod(temporary, 0o600)
                json.dump({"offset": offset}, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.offset_path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _brief(self) -> str:
        data = self.config.data_directory
        return BriefingService(self.config, AuditLog(data / "audit.jsonl"),
                               ApprovalQueue(data / "approvals.json"),
                               TrainingRegister(data / "training.json")).render()

    def _reply(self, text: str) -> str:
        command = text.strip().split(maxsplit=1)[0].lower() if text.strip() else ""
        if command in ("/start", "/help"):
            return ("Argos: send text or a photo (optional caption) in this private chat, or use /status, /brief, /market, /strategy SYMBOL, /review SYMBOL, /assess SYMBOL, /whales, /bybit, /account, /email, /emailfind QUERY, /forget, /help. "
                    "Only your private chat is accepted. No trading commands.")
        if command == "/status":
            pending = len(ArgosService(self.config).approvals.pending())
            enabled = self.config.section("trading").get("enabled", False)
            return (f"Argos {__version__} is responding. Pending approvals: {pending}. "
                    f"Live trading: {'enabled' if enabled else 'disabled'}. "
                    f"Market watch timer: {timer_state()}.")
        if command == "/brief":
            return self._brief()
        if command == "/market":
            return market_report(self.config.data_directory / "market.jsonl",
                                 self.config.section("trading").get("market_universe", []))
        if command == "/strategy":
            parts = text.strip().split(maxsplit=1)
            symbol = parts[1].strip() if len(parts) > 1 else ""
            return strategy_query(self.config.data_directory / "market.jsonl", symbol,
                                  self.config.section("trading").get("market_universe", []))
        if command == "/review":
            parts = text.strip().split(maxsplit=1)
            try:
                return review(parts[1] if len(parts) > 1 else "",
                              self.config.section("trading").get("market_universe", []))
            except (ReviewError, SubaccountError) as exc:
                return str(exc)
        if command == "/assess":
            parts = text.strip().split(maxsplit=1)
            try:
                return assess(parts[1] if len(parts) > 1 else "",
                              self.config.section("trading").get("market_universe", []))
            except ReviewError as exc:
                return str(exc)
        if command == "/whales":
            return digest(self.config.data_directory / "whalebot.json")
        if command == "/email":
            try:
                return GmailMetadataReader().summary()
            except ValueError as exc:
                return str(exc)
        if command == "/emailfind":
            parts = text.strip().split(maxsplit=1)
            if len(parts) == 1:
                return "Usage: /emailfind QUERY (searches inbox subjects and sender metadata)."
            try:
                return GmailMetadataReader().summary(parts[1])
            except ValueError as exc:
                return str(exc)
        if command == "/bybit":
            return testnet_balance()
        if command == "/account":
            try:
                return SubaccountReader().report()
            except SubaccountError as exc:
                return str(exc)
        return "Try /status, /brief, /market, /strategy SYMBOL, /review SYMBOL, /assess SYMBOL, /whales, /bybit, /account, /email, /emailfind QUERY, /forget, or send me a normal message."

    def poll_once(self) -> int:
        offset = self._offset()
        handled = 0
        ai_requests = 0
        for update in self._fetch(offset):
            update_id = update.get("update_id")
            if type(update_id) is not int or update_id < offset:
                continue
            message = update.get("message", {})
            chat = message.get("chat", {}) if isinstance(message, dict) else {}
            if (isinstance(chat, dict) and chat.get("type") == "private"
                    and chat.get("id") == self.owner_chat_id and isinstance(message, dict)):
                content = message.get("text", "")
                content = content.strip() if isinstance(content, str) else ""
                has_image = bool(message.get("photo")) or isinstance(message.get("document"), dict)
                if has_image or (content and not content.startswith("/")):
                    if ai_requests >= 1:
                        break  # Leave later messages for the next timer tick.
                    ai_requests += 1
                    try:
                        if has_image:
                            caption = message.get("caption", "")
                            caption = caption.strip() if isinstance(caption, str) else ""
                            answer, history = self.chat.draft(caption, image=self._image(message))
                        else:
                            answer, history = self.chat.draft(content)
                    except ChatUnavailable as exc:
                        answer, history = str(exc), None
                    except ValueError as exc:
                        answer, history = str(exc), None
                    self.sender.send(answer)
                    self.chat.commit(history)
                elif content and content.lower().split(maxsplit=1)[0] == "/forget":
                    self.chat.forget()
                    self.sender.send("Chat history cleared on this server.")
                else:
                    self.sender.send(self._reply(content))
                handled += 1
            offset = update_id + 1
            self._save_offset(offset)
        return handled
