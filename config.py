from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Config:
    raw: dict[str, Any]
    path: Path

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        resolved = Path(path).expanduser().resolve()
        with resolved.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        return cls(raw=raw, path=resolved)

    def section(self, name: str) -> dict[str, Any]:
        value = self.raw.get(name, {})
        if not isinstance(value, dict):
            raise ValueError(f"Configuration section {name!r} must be an object")
        return value

    @property
    def data_directory(self) -> Path:
        configured = self.section("storage").get("data_directory", "data")
        path = Path(configured)
        if not path.is_absolute():
            path = self.path.parent.parent / path
        path.mkdir(parents=True, exist_ok=True)
        return path.resolve()
