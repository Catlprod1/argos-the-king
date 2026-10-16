"""Reproducible, read-only mainnet spot market research from closed candles."""

from __future__ import annotations

import re
import time
import urllib.request
from decimal import Decimal

from .live_review import ReviewError, _get, _number

HOUR_MS = 3_600_000


def assess(symbol: str, allowed_symbols: list[str], opener=urllib.request.urlopen) -> str:
    symbol = symbol.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{4,20}", symbol) or symbol not in allowed_symbols:
        return "Use /assess BTCUSDT or another configured symbol."
    result, server_ms = _get("/v5/market/kline", {"category": "spot", "symbol": symbol,
                                "interval": "60", "limit": "25"}, opener)
    if result.get("symbol") != symbol or not isinstance(result.get("list"), list):
        raise ReviewError("Invalid market assessment response")
    try:
        candles = []
        for row in result["list"]:
            start = int(row[0])
            if start % HOUR_MS or start + HOUR_MS > server_ms:
                continue
            high, low, close, volume = (_number(row[i]) for i in (2, 3, 4, 5))
            if low > high or not low <= close <= high:
                raise ReviewError("Invalid market assessment candle")
            candles.append((start, high, low, close, volume))
        candles.sort(key=lambda row: row[0])
        recent = candles[-21:]
        if len(recent) != 21 or len({row[0] for row in recent}) != 21 or any(
            b[0] - a[0] != HOUR_MS for a, b in zip(recent, recent[1:])
        ) or server_ms - (recent[-1][0] + HOUR_MS) > HOUR_MS:
            raise ReviewError("Need 21 consecutive recent closed hourly candles")
        last = recent[-1]
        previous = recent[-13:-1]
        previous_high = max(row[1] for row in previous)
        volumes = sorted(row[4] for row in previous)
        median = (volumes[5] + volumes[6]) / 2
        average = sum((row[3] for row in recent[-20:]), Decimal(0)) / 20
        above = last[3] > average
        breakout = last[3] > previous_high
        volume_ok = median > 0 and last[4] >= 2 * median
        if breakout and volume_ok and above:
            classification = "Breakout research candidate (three checks met)"
        else:
            classification = "No confirmed breakout under this rule"
        age = max(0, int((server_ms - last[0] - HOUR_MS) / 60_000))
        end = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime((last[0] + HOUR_MS) / 1000))
        ratio = f"{last[4] / median:.2f}x" if median > 0 else "unavailable"
    except (IndexError, TypeError, ValueError, KeyError) as exc:
        if isinstance(exc, ReviewError):
            raise
        raise ReviewError("Invalid market assessment candle data") from None
    return (f"{symbol} MAINNET spot assessment — {classification}\n"
            f"Source: Bybit closed 1h candles; latest ended {end}; data age {age} min.\n"
            f"Close {last[3]} vs 20h average {average:.4f}: {'above' if above else 'not above'}.\n"
            f"Close vs previous 12h high {previous_high}: {'above' if breakout else 'not above'}.\n"
            f"Volume {last[4]} vs previous 12h median {median}: {ratio} "
            f"({'meets' if volume_ok else 'below'} 2x threshold).\n"
            "One venue and timeframe; no backtest, independent confirmation, funding, "
            "fees or slippage. Research only; no order or trade instruction.")
