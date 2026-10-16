"""Read-only summary of locally collected public market snapshots."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


def market_report(path: Path, symbols: list[str]) -> str:
    if not path.exists():
        return "No market snapshots yet. The market timer collects public data every 15 minutes."
    latest: dict[str, list[dict]] = {symbol: [] for symbol in symbols}
    with path.open(encoding="utf-8") as source:
        for line in source:
            try:
                item = json.loads(line)
                symbol = item.get("symbol")
                if symbol in latest:
                    latest[symbol].append(item)
                    latest[symbol] = latest[symbol][-2:]
            except (ValueError, AttributeError, TypeError):
                continue
    lines = ["Argos market snapshot (public Bybit Testnet spot; research only):"]
    for symbol, items in latest.items():
        if not items:
            lines.append(f"{symbol}: no data yet")
            continue
        try:
            current = items[-1]
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(current["fetched_at"])).total_seconds()
            if age < 0 or age > 3600:
                lines.append(f"{symbol}: latest snapshot is stale; no current analysis")
                continue
            price = Decimal(current["last_price"])
            bid, ask = Decimal(current["bid_price"]), Decimal(current["ask_price"])
            if price <= 0 or bid <= 0 or ask <= 0 or bid > ask:
                raise ValueError("invalid prices")
            line = f"{symbol}: last {price}; bid {bid}; ask {ask}; age {int(age // 60)} min"
            if len(items) == 2:
                previous = Decimal(items[0]["last_price"])
                if previous > 0:
                    change = (price / previous - 1) * 100
                    line += f"; change since previous snapshot {change:+.2f}%"
            lines.append(line)
        except (KeyError, ValueError, ArithmeticError):
            lines.append(f"{symbol}: invalid snapshot; no analysis")
    lines.append("A pair of prices cannot establish a trend or justify a trade.")
    return "\n".join(lines)
