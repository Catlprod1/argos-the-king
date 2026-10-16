"""Read-only public market feeds. No keys, no page scraping, no orders."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BINANCE = "https://api.binance.com/api/v3/ticker/24hr"
COINGECKO = "https://api.coingecko.com/api/v3/global"
FEAR_GREED = "https://api.alternative.me/fng/"
DXY = "https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB?interval=1h&range=1d"


def _get(url: str, opener) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Argos/0.8.13"}, method="GET")
    with opener(request, timeout=10) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("Feed did not return an object")
    return payload


def binance_tape(symbols: list[str], opener=urllib.request.urlopen) -> str:
    lines = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    for symbol in symbols[:6]:
        if not symbol.isalnum():
            continue
        payload = _get(BINANCE + "?" + urllib.parse.urlencode({"symbol": symbol}), opener)
        if payload.get("symbol") != symbol:
            raise ValueError("Binance returned a different symbol")
        last = payload.get("lastPrice")
        change = payload.get("priceChangePercent")
        if not last or change is None:
            raise ValueError("Binance ticker was incomplete")
        lines.append(f"{symbol} last {last}; 24h {change}%; as of {now}.")
    return "Binance public spot (read-only):\n" + "\n".join(lines) if lines else ""


def dxy_quote(opener=urllib.request.urlopen) -> str:
    """ICE US Dollar Index via Yahoo's public chart endpoint. Read-only."""
    payload = _get(DXY, opener)
    result = payload["chart"]["result"][0]
    meta = result["meta"]
    if meta.get("symbol") != "DX-Y.NYB":
        raise ValueError("Dollar index symbol mismatch")
    price = meta.get("regularMarketPrice")
    if not isinstance(price, (int, float)) or price <= 0:
        raise ValueError("Dollar index price missing")
    previous = meta.get("chartPreviousClose") or meta.get("previousClose")
    change = ""
    if isinstance(previous, (int, float)) and previous > 0:
        change = f"; session {((price / previous) - 1) * 100:+.2f}% vs previous close {previous}"
    state = meta.get("marketState") or "unknown"
    stamp = datetime.fromtimestamp(int(meta.get("regularMarketTime") or datetime.now(timezone.utc).timestamp()), timezone.utc)
    return (f"DXY ICE Dollar Index (DX-Y.NYB) last {price}{change}; market {state}; "
            f"as of {stamp.strftime('%Y-%m-%d %H:%M UTC')}. Not 24/7; closed weekends and the daily ICE break.")


def market_context(opener=urllib.request.urlopen) -> str:
    """BTC dominance and the fear/greed print. Either source can fail alone."""
    lines = []
    try:
        payload = _get(COINGECKO, opener)
        data = payload["data"]
        dominance = data["market_cap_percentage"]["btc"]
        change = data["market_cap_change_percentage_24h_usd"]
        lines.append(f"CoinGecko global: BTC dominance {dominance:.2f}%; total market cap 24h {change:+.2f}%.")
    except (OSError, ValueError, KeyError, TypeError):
        lines.append("CoinGecko global: unavailable.")
    try:
        payload = _get(FEAR_GREED + "?" + urllib.parse.urlencode({"limit": "1"}), opener)
        item = payload["data"][0]
        lines.append(f"Alternative.me fear/greed: {item['value']} ({item['value_classification']}).")
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        lines.append("Fear/greed: unavailable.")
    return "Other read-only feeds:\n" + "\n".join(lines)


def extra_feeds(symbols: list[str], opener=urllib.request.urlopen) -> str:
    parts = []
    try:
        tape = binance_tape(symbols, opener)
        if tape:
            parts.append(tape)
    except (OSError, ValueError, KeyError, TypeError):
        parts.append("Binance public spot: unavailable. Do not invent a Binance price.")
    parts.append(market_context(opener))
    try:
        parts.append(dxy_quote(opener))
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        parts.append("DXY: unavailable. Do not invent a dollar-index price.")
    parts.append("These feeds are evidence only. They do not override the Bybit tape or owner notes.")
    return "\n".join(parts)
