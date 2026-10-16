"""Hourly public mainnet spot candle watcher. Read-only, deterministic alerts."""

from __future__ import annotations

import json
import os
import tempfile
import time
from decimal import Decimal
from pathlib import Path

from .live_review import ReviewError, _get, _number
from .notifier import TelegramNotifier


def watch(symbols: list[str], state_path: Path, sender: TelegramNotifier,
          opener=None) -> int:
    """Alert on a closed hourly breakout with 2x prior median volume.

    One alert per symbol and closed candle; state is committed after delivery.
    No model calls, account keys, orders, or unfinished candles.
    """
    if opener is None:
        import urllib.request
        opener = urllib.request.urlopen
    if not isinstance(symbols, list) or not symbols or len(symbols) > 10 or any(
        not isinstance(s, str) or not s.endswith("USDT") or not s[:-4].isalnum()
        or not s.isupper() or len(s) > 20 for s in symbols
    ) or len(set(symbols)) != len(symbols):
        raise ValueError("Market watch needs 1-10 unique configured USDT symbols")
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        state = {}
    if not isinstance(state, dict) or any(not isinstance(k, str) or type(v) is not int
                                               or v < 0 for k, v in state.items()):
        raise ValueError("Invalid market watch state")
    sent = 0
    for symbol in symbols:
        result, server_ms = _get("/v5/market/kline", {"category": "spot", "symbol": symbol,
                                  "interval": "60", "limit": "20"}, opener)
        if result.get("symbol") != symbol or not isinstance(result.get("list"), list):
            raise ReviewError("Bybit returned a different symbol or invalid candles")
        try:
            candles = []
            for row in result["list"]:
                start = int(row[0])
                if start % 3_600_000 or start + 3_600_000 > server_ms:
                    continue
                candles.append((start, _number(row[2]), _number(row[4]), _number(row[5])))
            candles.sort(key=lambda c: c[0])
            if len(candles) < 13 or len({c[0] for c in candles}) != len(candles):
                raise ReviewError("Insufficient distinct closed candles")
            last = candles[-1]
            prior = candles[-13:-1]
            if server_ms - (last[0] + 3_600_000) > 3_900_000 or any(
                b[0] - a[0] != 3_600_000 for a, b in zip(candles[-13:-1], candles[-12:])
            ):
                raise ReviewError("Market watch candles are stale or have gaps")
            volumes = sorted(c[3] for c in prior)
            median = (volumes[5] + volumes[6]) / 2
            breakout = last[2] > max(c[1] for c in prior)
            high_volume = median > 0 and last[3] >= 2 * median
        except (IndexError, TypeError, ValueError, KeyError) as exc:
            if isinstance(exc, ReviewError):
                raise
            raise ReviewError("Invalid market watch candle data") from None
        if not (breakout and high_volume) or last[0] <= state.get(symbol, 0):
            continue
        ended = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime((last[0] + 3_600_000) / 1000))
        age = max(0, int((server_ms - last[0] - 3_600_000) / 60_000))
        sender.send(f"Argos market watch — {symbol} MAINNET spot (research only)\n"
                    f"Closed hourly candle: {ended}; data age: {age} min.\n"
                    f"Close: {last[2]}; prior 12h high: {max(c[1] for c in prior)}.\n"
                    f"Volume: {last[3]}; prior 12h median: {median} ({last[3] / median:.2f}x).\n"
                    "Rule: close above prior 12h high AND volume >= 2x median. "
                    "No trade signal, account action, or execution.")
        state[symbol] = last[0]
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=state_path.parent,
                                             prefix="market-watch-", delete=False) as output:
                temporary = Path(output.name)
                os.chmod(temporary, 0o600)
                json.dump(state, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, state_path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        sent += 1
    return sent
