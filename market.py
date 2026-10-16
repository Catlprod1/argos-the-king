"""Public, read-only Bybit ticker snapshots. No authenticated endpoints."""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

BASE_URL = "https://api-testnet.bybit.com"
TICKERS_PATH = "/v5/market/tickers"


class MarketDataError(ValueError):
    pass


def positive_decimal(value: object, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise MarketDataError(f"Invalid {field}") from exc
    if not result.is_finite() or result <= 0:
        raise MarketDataError(f"Invalid {field}")
    return result


@dataclass(frozen=True)
class Ticker:
    source: str
    symbol: str
    fetched_at: str
    server_time_ms: int
    last_price: str
    bid_price: str
    ask_price: str
    volume_24h: str


class BybitPublicMarket:
    def __init__(self, allowed_symbols: list[str], opener=urllib.request.urlopen):
        self.allowed_symbols = set(allowed_symbols)
        self.opener = opener

    def ticker(self, symbol: str) -> Ticker:
        if not re.fullmatch(r"[A-Z0-9]{4,20}", symbol) or symbol not in self.allowed_symbols:
            raise MarketDataError("Symbol is not allow-listed")
        url = BASE_URL + TICKERS_PATH + "?" + urllib.parse.urlencode(
            {"category": "spot", "symbol": symbol}
        )
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with self.opener(request, timeout=10) as response:
                body = json.load(response)
        except (OSError, ValueError) as exc:
            raise MarketDataError("Market data request failed") from exc
        if body.get("retCode") != 0:
            raise MarketDataError("Bybit rejected the market data request")
        try:
            server_time_ms = int(body["time"])
            items = body["result"]["list"]
            if len(items) != 1 or items[0]["symbol"] != symbol:
                raise MarketDataError("Unexpected ticker symbol or count")
            item = items[0]
            last = positive_decimal(item["lastPrice"], "last price")
            bid = positive_decimal(item["bid1Price"], "bid price")
            ask = positive_decimal(item["ask1Price"], "ask price")
            volume = Decimal(str(item["volume24h"]))
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise MarketDataError("Invalid ticker response") from exc
        if not volume.is_finite() or volume < 0 or bid > ask:
            raise MarketDataError("Invalid market or crossed bid/ask")
        now = datetime.now(timezone.utc)
        if abs(now.timestamp() * 1000 - server_time_ms) > 120_000:
            raise MarketDataError("Stale or future-dated server response")
        return Ticker(BASE_URL + TICKERS_PATH, symbol, now.isoformat(),
                      server_time_ms, str(last), str(bid), str(ask), str(volume))


def append_ticker(path: Path, ticker: Ticker) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(ticker), sort_keys=True) + "\n")


def latest_ticker(path: Path, symbol: str, max_age_seconds: int = 120) -> Ticker:
    if not path.exists():
        raise MarketDataError("No market snapshot stored")
    with path.open(encoding="utf-8") as handle:
        lines = handle.readlines()
    for line in reversed(lines):
        item = json.loads(line)
        if item.get("symbol") == symbol:
            ticker = Ticker(**item)
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(ticker.fetched_at)).total_seconds()
            if age < 0 or age > max_age_seconds:
                raise MarketDataError("Market snapshot has expired")
            return ticker
    raise MarketDataError("No snapshot for symbol")
