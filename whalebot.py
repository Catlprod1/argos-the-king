"""Sample the public WhaleBot Pumps Telegram preview without joining or trading."""

from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

FEED_URL = "https://t.me/s/whalebotpumps"
CHANNEL = "whalebotpumps"
MAX_PAGE_BYTES = 700_000


class FeedError(ValueError):
    pass


class PreviewParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.posts: list[dict[str, str]] = []
        self.current: dict[str, str] | None = None
        self.depth = 0
        self.text_depth = 0
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        post = attributes.get("data-post")
        if self.current is None and post and re.fullmatch(r"whalebotpumps/[0-9]+", post):
            self.current = {"id": post, "published": ""}
            self.depth = 1
        elif self.current is not None and tag not in {"area", "br", "hr", "img", "input", "link", "meta", "source"}:
            self.depth += 1
        if self.current is None:
            return
        if tag == "time" and attributes.get("datetime"):
            self.current["published"] = attributes["datetime"] or ""
        if "tgme_widget_message_text" in classes:
            self.text_depth = self.depth
            self.text_parts = []
        if self.text_depth and tag == "br":
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.text_depth:
            self.text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.current is None:
            return
        if tag in {"area", "br", "hr", "img", "input", "link", "meta", "source"}:
            return
        if self.text_depth == self.depth:
            self.current["text"] = "".join(self.text_parts).strip()[:600]
            self.text_depth = 0
        self.depth -= 1
        if self.depth == 0:
            if self.current.get("text") and self.current.get("published"):
                self.posts.append(self.current)
            self.current = None


def parse_preview(page: str) -> list[dict[str, str]]:
    parser = PreviewParser()
    parser.feed(page)
    unique = {item["id"]: item for item in parser.posts}
    return list(unique.values())[-30:]


def _write(path: Path, data: dict) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="whalebot-", delete=False) as output:
            temporary = Path(output.name)
            os.chmod(temporary, 0o600)
            json.dump(data, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def collect(path: Path, opener=urllib.request.urlopen) -> int:
    request = urllib.request.Request(FEED_URL, headers={"User-Agent": "Mozilla/5.0 ArgosResearch/0.8"})
    try:
        with opener(request, timeout=15) as response:
            raw = response.read(MAX_PAGE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError):
        raise FeedError("Public WhaleBot preview was unreachable") from None
    if len(raw) > MAX_PAGE_BYTES:
        raise FeedError("Public WhaleBot preview exceeded the size limit")
    posts = parse_preview(raw.decode("utf-8", errors="replace"))
    if not posts:
        raise FeedError("Public WhaleBot preview contained no readable posts")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        state = None
    if state is not None and (not isinstance(state, dict) or not isinstance(state.get("seen"), list)
                              or not isinstance(state.get("recent"), list)):
        raise FeedError("WhaleBot state needs repair")
    seen = set(state["seen"]) if state else set()
    new = [p for p in posts if p["id"] not in seen]
    # On first run, establish a baseline without presenting historical posts as new.
    recent = (state["recent"] + new)[-50:] if state else []
    _write(path, {"checked_at": datetime.now(timezone.utc).isoformat(),
                  "seen": list(dict.fromkeys((state["seen"] if state else []) + [p["id"] for p in posts]))[-120:],
                  "recent": recent})
    return len(new) if state else 0


def digest(path: Path) -> str:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        checked = datetime.fromisoformat(state["checked_at"])
        recent = state["recent"]
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        return "WhaleBot collector has no valid data yet. Check its scheduled service."
    age = (datetime.now(timezone.utc) - checked).total_seconds()
    if age < 0 or age > 3600:
        return "WhaleBot collector data is stale. Check its scheduled service."
    lines = ["WhaleBot public posts (unverified claims; no trade signal):"]
    for post in recent[-5:]:
        try:
            published = datetime.fromisoformat(post["published"].replace("Z", "+00:00"))
            if (datetime.now(timezone.utc) - published).total_seconds() > 86_400:
                continue
            excerpt = " ".join(post["text"].split())[:180]
            lines.append(f'- {published:%H:%M UTC} {excerpt} https://t.me/{post["id"]}')
        except (ValueError, KeyError, TypeError):
            continue
    if len(lines) == 1:
        lines.append("No new posts recorded since monitoring began.")
    return "\n".join(lines)
