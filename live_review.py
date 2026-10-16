"""On-demand mainnet market and verified subaccount review; GET requests only."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation

from .bybit_subaccount import SubaccountReader

BASE = "https://api.bybit.com"
PATHS = ("/v5/market/tickers", "/v5/market/kline", "/v5/market/orderbook")


class ReviewError(ValueError):
    pass


def _number(value: object) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ReviewError("Bybit returned invalid price data") from None
    if not number.is_finite() or number <= 0:
        raise ReviewError("Bybit returned invalid price data")
    return number


def _get(path: str, params: dict[str, str], opener) -> tuple[dict, int]:
    if path not in PATHS:
        raise ReviewError("Market endpoint is not permitted")
    request = urllib.request.Request(BASE + path + "?" + urllib.parse.urlencode(params),
                                     headers={"Accept": "application/json"}, method="GET")
    try:
        with opener(request, timeout=10) as response:
            payload = json.load(response)
        if payload.get("retCode") != 0 or not isinstance(payload.get("result"), dict):
            raise ReviewError("Bybit market request was rejected")
        server_ms = int(payload["time"])
        if abs(time.time() * 1000 - server_ms) > 120_000:
            raise ReviewError("Bybit market data is stale")
        return payload["result"], server_ms
    except (OSError, ValueError, TypeError, KeyError) as exc:
        if isinstance(exc, ReviewError):
            raise
        raise ReviewError("Bybit market request failed or returned invalid data") from None


def review(symbol: str, allowed_symbols: list[str], opener=urllib.request.urlopen,
           account: SubaccountReader | None = None) -> str:
    symbol = symbol.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{4,20}", symbol) or symbol not in allowed_symbols:
        return "Use /review BTCUSDT or another configured symbol."

    ticker, server_ms = _get(PATHS[0], {"category": "spot", "symbol": symbol}, opener)
    candles, _ = _get(PATHS[1], {"category": "spot", "symbol": symbol,
                                  "interval": "60", "limit": "20"}, opener)
    book, _ = _get(PATHS[2], {"category": "spot", "symbol": symbol,
                              "limit": "25"}, opener)
    try:
        item, = ticker["list"]
        if item["symbol"] != symbol or candles["symbol"] != symbol or book["s"] != symbol:
            raise ReviewError("Bybit returned a different symbol")
        last = _number(item["lastPrice"])
        bid, ask = _number(item["bid1Price"]), _number(item["ask1Price"])
        if bid > ask:
            raise ReviewError("Bybit returned a crossed market")
        # Bybit klines are newest first. Exclude the currently forming candle.
        closed = sorted((int(row[0]), _number(row[4])) for row in candles["list"]
                        if len(row) >= 5 and int(row[0]) + 3_600_000 <= server_ms)
        if len(closed) < 2 or server_ms - closed[-1][0] > 7_200_000:
            raise ReviewError("Insufficient recent closed hourly candles")
        hourly = (closed[-1][1] / closed[-2][1] - 1) * 100
        spread = (ask - bid) / ask * 100
        bids = sum((_number(row[0]) * _number(row[1]) for row in book["b"][:10]), Decimal(0))
        asks = sum((_number(row[0]) * _number(row[1]) for row in book["a"][:10]), Decimal(0))
        if bids <= 0 or asks <= 0:
            raise ReviewError("Incomplete order book")
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        if isinstance(exc, ReviewError):
            raise
        raise ReviewError("Bybit returned incomplete market data") from None

    account_report = (account or SubaccountReader()).report()
    return (f"{symbol} mainnet spot review (as of {time.strftime('%H:%M UTC', time.gmtime(server_ms / 1000))}):\n"
            f"Last {last}; bid {bid}; ask {ask}; spread {spread:.3f}%.\n"
            f"Last two closed hourly candles: {hourly:+.2f}% close-to-close. "
            f"Top 10 book levels: bid/ask notional {bids / asks:.2f}x (snapshot only).\n"
            f"{account_report}\n"
            "Price moves and order book depth do not establish a trade. No orders or transfers.")


def tape(allowed_symbols: list[str], opener=urllib.request.urlopen) -> str:
    """Short public mainnet spot tape for every owner chat turn."""
    symbols = [symbol for symbol in allowed_symbols if re.fullmatch(r"[A-Z0-9]{4,20}", symbol)][:6]
    if not symbols:
        return "No allow-listed symbols configured."
    lines = ["LIVE Bybit mainnet spot tape (quote only these prices):"]
    for symbol in symbols:
        result, server_ms = _get(PATHS[0], {"category": "spot", "symbol": symbol}, opener)
        try:
            item, = result["list"]
            if item["symbol"] != symbol:
                raise ReviewError("Bybit returned a different symbol")
            last = _number(item["lastPrice"])
            bid, ask = _number(item["bid1Price"]), _number(item["ask1Price"])
            if bid > ask:
                raise ReviewError("Bybit returned a crossed market")
            change = Decimal(str(item.get("price24hPcnt", "0"))) * 100
            if not change.is_finite():
                raise ReviewError("Bybit returned an invalid 24h change")
            stamp = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(server_ms / 1000))
        except (KeyError, TypeError, ValueError, IndexError, InvalidOperation) as exc:
            if isinstance(exc, ReviewError):
                raise
            raise ReviewError("Bybit returned incomplete ticker data") from None
        lines.append(f"{symbol} last {last}; bid {bid}; ask {ask}; 24h {change:+.2f}%; as of {stamp}.")
    lines.append("Testnet /market prices are not this tape. No order is implied.")
    return "\n".join(lines)


def owner_experience(data_directory, limit: int = 1200) -> str:
    """Owner notes plus recent paper theses. Missing files are not an error."""
    from pathlib import Path
    root = Path(data_directory)
    parts = []
    notes = root / "owner-experience.txt"
    if notes.exists():
        text = notes.read_text(encoding="utf-8").strip()
        if text:
            parts.append("Owner experience notes (preferences, not a price feed):\n" + text[:limit])
    ledger = root / "paper.jsonl"
    theses = []
    if ledger.exists():
        for line in ledger.read_text(encoding="utf-8").splitlines()[-8:]:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            thesis = str(event.get("thesis") or "").strip()
            if thesis:
                theses.append(f"{event.get('symbol', '?')}: {thesis[:160]}")
    if theses:
        parts.append("Recent paper theses:\n" + "\n".join(theses[-5:]))
    return "\n".join(parts)


def chat_bundle(message: str, allowed_symbols: list[str], data_directory) -> str:
    """Always attach the live tape; add a deeper review only when the question asks."""
    try:
        parts = [tape(allowed_symbols)]
    except ReviewError as exc:
        parts = [f"Live tape unavailable: {exc}. Do not invent a price."]
    extra = chat_context(message, allowed_symbols)
    if extra:
        parts.append(extra)
    experience = owner_experience(data_directory)
    if experience:
        parts.append(experience)
    from .feeds import extra_feeds
    parts.append(extra_feeds(allowed_symbols))
    return "\n".join(parts)


def chat_context(message: str, allowed_symbols: list[str]) -> str | None:
    """Fetch narrow, read-only context only for an explicit account or market question."""
    lower = message.lower()
    account_question = bool(re.search(r"\b(account|balance|positions?|equity|holdings?|wallet|portfolio|subaccount)\b", lower))
    market_question = bool(re.search(r"\b(market|price|chart|candles?|spread|order ?book|trends?|review)\b", lower))
    assessment_question = bool(re.search(r"\b(assess|assessment|outlook|setup|signals?|decisions?|decide|opportunity|breakout)\b", lower))
    if not (account_question or market_question or assessment_question):
        return None
    mentioned = [symbol for symbol in allowed_symbols if re.search(
        rf"\b(?:{re.escape(symbol)}|{re.escape(symbol.removesuffix('USDT'))})\b", message, re.I)]
    if mentioned and assessment_question:
        from .market_assessment import assess
        return assess(mentioned[0], allowed_symbols)
    if mentioned and market_question:
        return review(mentioned[0], allowed_symbols)
    if account_question:
        return SubaccountReader().report()
    return "No symbol specified. Ask the owner to name one configured market symbol; /assess SYMBOL uses current closed candles."
