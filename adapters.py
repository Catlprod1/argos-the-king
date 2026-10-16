from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class MarketOpportunity:
    symbol: str
    thesis: str
    confidence: float
    proposed_entry: float | None = None
    proposed_stop: float | None = None
    proposed_target: float | None = None


class EmailReader(Protocol):
    def search(self, query: str) -> list[dict]: ...


class MarketReader(Protocol):
    def opportunities(self) -> list[MarketOpportunity]: ...


class GmailReadOnlyAdapter:
    """Connector contract. OAuth setup is intentionally not stored here."""

    def search(self, query: str) -> list[dict]:
        raise NotImplementedError("Connect Gmail with read-only OAuth before use")


class BybitTestnetAdapter:
    """Testnet-only adapter contract; live order placement is absent from MVP."""

    def opportunities(self) -> list[MarketOpportunity]:
        return []
