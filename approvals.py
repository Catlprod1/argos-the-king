from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import Action, PolicyResult


class ApprovalQueue:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self, items: list[dict[str, Any]]) -> None:
        self.path.write_text(json.dumps(items, indent=2), encoding="utf-8")

    def add(self, action: Action, policy: PolicyResult) -> None:
        items = self._load()
        items.append({"action": action.to_dict(), "policy": policy.to_dict(), "status": "pending"})
        self._save(items)

    def pending(self) -> list[dict[str, Any]]:
        return [item for item in self._load() if item.get("status") == "pending"]

    def decide(self, action_id: str, approved: bool) -> bool:
        items = self._load()
        found = False
        for item in items:
            if item.get("action", {}).get("id") == action_id and item.get("status") == "pending":
                item["status"] = "approved" if approved else "rejected"
                found = True
                break
        if found:
            self._save(items)
        return found
