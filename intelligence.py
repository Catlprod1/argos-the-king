from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class Signal:
    source: str
    source_type: str
    asset: str
    direction: float
    confidence: float
    independent_group: str
    observed_at: str = ""

    def __post_init__(self):
        if not self.asset or not self.independent_group:
            raise ValueError("asset and independent source group are required")
        if not -1 <= self.direction <= 1:
            raise ValueError("direction must be between -1 and 1")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if not self.observed_at:
            object.__setattr__(self, "observed_at", datetime.now(timezone.utc).isoformat())
        observed = datetime.fromisoformat(self.observed_at)
        if observed.tzinfo is None:
            raise ValueError("observed_at must include a timezone")


@dataclass(frozen=True)
class SignalAssessment:
    score: float
    independent_sources: int
    market_confirmed: bool
    trade_eligible: bool
    explanation: str


class SignalFusion:
    def __init__(
        self,
        minimum_independent_sources: int = 2,
        social_signal_max_weight: float = 0.25,
        require_market_confirmation: bool = True,
    ):
        self.minimum_independent_sources = minimum_independent_sources
        self.social_signal_max_weight = social_signal_max_weight
        self.require_market_confirmation = require_market_confirmation

    def assess(self, signals: list[Signal]) -> SignalAssessment:
        if not signals:
            return SignalAssessment(0, 0, False, False, "No signals")

        if len({signal.asset for signal in signals}) != 1:
            return SignalAssessment(0, 0, False, False, "Signals concern different assets")

        now = datetime.now(timezone.utc)
        fresh = [signal for signal in signals if
                 timedelta(0) <= now - datetime.fromisoformat(signal.observed_at)
                 <= timedelta(hours=24)]
        if len(fresh) != len(signals):
            return SignalAssessment(0, 0, False, False, "Stale or future-dated evidence")

        # A coordinated promotion is one source, however many posts it makes.
        by_group: dict[str, Signal] = {}
        for signal in fresh:
            current = by_group.get(signal.independent_group)
            if current is None or signal.confidence > current.confidence:
                by_group[signal.independent_group] = signal

        weighted_sum = 0.0
        total_weight = 0.0
        independent_groups = set()
        market_confirmed = False
        market_directions = []
        for signal in by_group.values():
            weight = signal.confidence
            if signal.source_type in {"telegram", "social", "x"}:
                weight = min(weight, self.social_signal_max_weight)
            if signal.source_type in {"price", "orderbook", "volume", "onchain"}:
                if (now - datetime.fromisoformat(signal.observed_at)
                        <= timedelta(minutes=15)):
                    market_directions.append(signal.direction)
            weighted_sum += signal.direction * weight
            total_weight += weight
            independent_groups.add(signal.independent_group)

        score = weighted_sum / total_weight if total_weight else 0.0
        market_confirmed = any(direction * score > 0 for direction in market_directions)
        contradictory_market = any(direction * score < 0 for direction in market_directions)
        enough_sources = len(independent_groups) >= self.minimum_independent_sources
        eligible = (enough_sources and abs(score) >= 0.2 and
                    not contradictory_market and
                    (market_confirmed or not self.require_market_confirmation))
        reasons = []
        if not enough_sources:
            reasons.append("insufficient independent sources")
        if self.require_market_confirmation and not market_confirmed:
            reasons.append("no market confirmation")
        if contradictory_market:
            reasons.append("contradictory market evidence")
        if abs(score) < 0.2:
            reasons.append("insufficient directional conviction")
        return SignalAssessment(
            score=score,
            independent_sources=len(independent_groups),
            market_confirmed=market_confirmed,
            trade_eligible=eligible,
            explanation="eligible" if eligible else "; ".join(reasons),
        )
