from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class TrainingRecord:
    course: str
    provider: str
    completed_on: str
    expires_on: str | None = None
    certificate_path: str | None = None
    certificate_number: str | None = None


class TrainingRegister:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[dict]:
        if not self.path.exists():
            return []
        return json.loads(self.path.read_text(encoding="utf-8"))

    def add(self, record: TrainingRecord) -> None:
        records = self.list()
        records.append(asdict(record))
        self.path.write_text(json.dumps(records, indent=2), encoding="utf-8")
