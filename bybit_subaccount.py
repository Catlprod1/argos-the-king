"""Read-only mainnet AI subaccount inspection; never send an order or transfer."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode

BASE = "https://api.bybit.com"


class SubaccountError(ValueError):
    pass


class SubaccountReader:
    def __init__(self, opener=urllib.request.urlopen):
        self.opener = opener
        self.key = os.environ.get("ARGOS_BYBIT_AI_KEY", "").strip()
        self.secret = os.environ.get("ARGOS_BYBIT_AI_SECRET", "").strip()
        self.uid = os.environ.get("ARGOS_BYBIT_AI_SUB_UID", "").strip()

    def _get(self, path: str, params: dict[str, str] | None = None) -> dict:
        if path not in {"/v5/user/query-api", "/v5/account/wallet-balance", "/v5/position/list"}:
            raise SubaccountError("Endpoint is not allowed")
        query = urlencode(params or {})
        timestamp, window = str(int(time.time() * 1000)), "5000"
        signature = hmac.new(self.secret.encode(), (timestamp + self.key + window + query).encode(), hashlib.sha256).hexdigest()
        request = urllib.request.Request(BASE + path + ("?" + query if query else ""),
            headers={"X-BAPI-API-KEY": self.key, "X-BAPI-TIMESTAMP": timestamp,
                     "X-BAPI-RECV-WINDOW": window, "X-BAPI-SIGN": signature}, method="GET")
        try:
            with self.opener(request, timeout=15) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            raise SubaccountError(f"Bybit returned HTTP {exc.code}; check account access") from None
        except (OSError, ValueError, TypeError):
            raise SubaccountError("Bybit account request failed") from None
        if not isinstance(payload, dict) or payload.get("retCode") != 0:
            code = payload.get("retCode") if isinstance(payload, dict) else None
            raise SubaccountError(f"Bybit rejected the account request (code {code})")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise SubaccountError("Bybit returned invalid account data")
        return result

    def report(self) -> str:
        if not self.key or not self.secret or not self.uid:
            return "Bybit AI subaccount is not connected. Provide its credentials and expected subaccount UID on the server, never in Telegram."
        if not self.uid.isdecimal():
            raise SubaccountError("Expected Bybit subaccount UID must be numeric")
        identity = self._get("/v5/user/query-api")
        if (str(identity.get("userID")) != self.uid or identity.get("isMaster") is not False
                or str(identity.get("parentUid", "0")) == "0"):
            raise SubaccountError("Bybit key is not for the selected subaccount; account reads blocked")
        account = self._get("/v5/account/wallet-balance", {"accountType": "UNIFIED"})
        accounts = account.get("list", [])
        if not isinstance(accounts, list) or not accounts or not isinstance(accounts[0], dict):
            raise SubaccountError("Bybit returned no unified account")
        wallet = accounts[0]
        lines = [f"Bybit AI subaccount {self.uid} [MAINNET; read-only requests]",
                 f"Equity: {str(wallet.get('totalEquity', 'unknown'))[:40]} USDT"]
        coins = wallet.get("coin", [])
        if isinstance(coins, list):
            for coin in coins[:8]:
                if isinstance(coin, dict):
                    lines.append(f"{str(coin.get('coin', '?'))[:12]} balance: {str(coin.get('walletBalance', '?'))[:40]}")
        positions = self._get("/v5/position/list", {"category": "linear", "settleCoin": "USDT"})
        items = positions.get("list", [])
        if not isinstance(items, list):
            raise SubaccountError("Bybit returned invalid positions")
        active = [item for item in items if isinstance(item, dict) and str(item.get("size", "0")) not in ("", "0")]
        lines.append(f"Open USDT linear positions: {len(active)}" + ("+" if len(active) > 5 else ""))
        for item in active[:5]:
            lines.append(f"- {str(item.get('symbol', '?'))[:20]} {str(item.get('side', '?'))[:8]} size {str(item.get('size', '?'))[:24]}")
        lines.append("Read-only view. No orders, transfers or trading advice.")
        return "\n".join(lines)
