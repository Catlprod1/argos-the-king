"""Bybit Testnet read-only wallet endpoint; no order or transfer methods."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request


def testnet_balance(opener=urllib.request.urlopen) -> str:
    key = os.environ.get("ARGOS_BYBIT_TESTNET_KEY", "")
    secret = os.environ.get("ARGOS_BYBIT_TESTNET_SECRET", "")
    if not key or not secret:
        return "Bybit account is not connected. /market uses public Testnet prices without a key."
    timestamp = str(int(time.time() * 1000))
    query = "accountType=UNIFIED"
    window = "5000"
    signature = hmac.new(secret.encode(), (timestamp + key + window + query).encode(),
                         hashlib.sha256).hexdigest()
    request = urllib.request.Request(
        "https://api-testnet.bybit.com/v5/account/wallet-balance?" + query,
        headers={"X-BAPI-API-KEY": key, "X-BAPI-TIMESTAMP": timestamp,
                 "X-BAPI-RECV-WINDOW": window, "X-BAPI-SIGN": signature})
    try:
        with opener(request, timeout=15) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        return f"Bybit Testnet returned HTTP {exc.code}; check read-only API access."
    except (OSError, ValueError):
        return "Bybit Testnet account request failed."
    if not isinstance(data, dict) or data.get("retCode") != 0:
        return "Bybit Testnet did not accept the read-only wallet request."
    try:
        account = data["result"]["list"][0]
        equity = account.get("totalEquity", "unknown")
        coins = [f'{coin["coin"]}: {coin.get("walletBalance", "unknown")}'
                 for coin in account.get("coin", [])[:8]]
    except (KeyError, IndexError, TypeError):
        return "Bybit Testnet returned an unexpected wallet format."
    return "Bybit Testnet UNIFIED account (read-only): total equity " + str(equity)[:40] + (
        "; " + ", ".join(coins) if coins else "; no nonzero coin balances returned")
