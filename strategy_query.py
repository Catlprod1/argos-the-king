"""Research checklist over locally collected market data, without trade signals."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path


def strategy_query(path: Path, symbol: str, allowed: list[str]) -> str:
    symbol = symbol.upper()
    if not re.fullmatch(r"[A-Z0-9]{3,20}", symbol) or symbol not in allowed:
        return "Choose a configured symbol, such as: " + ", ".join(allowed[:8])
    if not path.exists():
        return f"{symbol}: no stored snapshots. No strategy assessment."
    observations = []
    with path.open(encoding="utf-8") as source:
        for line in source:
            try:
                item = json.loads(line)
                if item.get("symbol") == symbol:
                    observations.append(item)
                    observations = observations[-8:]
            except (ValueError, TypeError, AttributeError):
                continue
    if not observations:
        return f"{symbol}: no stored snapshots. No strategy assessment."
    try:
        latest = observations[-1]
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(latest["fetched_at"])).total_seconds()
        price, bid, ask = (Decimal(latest[field]) for field in ("last_price", "bid_price", "ask_price"))
        if age < 0 or age > 3600 or min(price, bid, ask) <= 0 or bid > ask:
            raise ValueError("stale or invalid")
        spread = (ask - bid) / ask * 100
        lines = [f"{symbol} research checklist (public Testnet spot):",
                 f"Last {price}; spread {spread:.3f}%; snapshot age {int(age // 60)} min."]
        if len(observations) > 1:
            prior = Decimal(observations[-2]["last_price"])
            if prior > 0:
                lines.append(f"Change since prior snapshot: {(price / prior - 1) * 100:+.2f}% (not a trend).")
        lines.append("Strategy needs a defined entry, exit, risk limit, independent confirmation and backtest. No trade signal or execution.")
        return "\n".join(lines)
    except (KeyError, ValueError, InvalidOperation, ArithmeticError):
        return f"{symbol}: stored data is stale or invalid. No strategy assessment."
