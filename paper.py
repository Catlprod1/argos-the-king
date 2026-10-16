"""Append-only spot-long paper ledger. Simulated fills only, no order API."""

from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from .market import BASE_URL, TICKERS_PATH, Ticker, positive_decimal


class PaperError(ValueError):
    pass


class PaperLedger:
    def __init__(self, path: Path, equity_usdt: str = "1000",
                 max_risk_usdt: str = "5", max_total_open_risk_usdt: str = "20",
                 fee_rate: str = "0.001",
                 slippage_rate: str = "0.001"):
        self.path = path
        self.equity = positive_decimal(equity_usdt, "paper equity")
        self.max_risk = positive_decimal(max_risk_usdt, "paper risk cap")
        self.max_total_open_risk = positive_decimal(max_total_open_risk_usdt, "total paper risk cap")
        self.fee = Decimal(str(fee_rate))
        self.slippage = Decimal(str(slippage_rate))
        if (not self.fee.is_finite() or not self.slippage.is_finite()
                or not 0 <= self.fee < 0.1 or not 0 <= self.slippage < 0.1):
            raise PaperError("Invalid simulated fee or slippage rate")

    def events(self) -> list[dict]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def _record(self, event: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            handle.write(json.dumps(event, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            fcntl.flock(handle, fcntl.LOCK_UN)

    @staticmethod
    def _validate_ticker(ticker: Ticker) -> None:
        if ticker.source != BASE_URL + TICKERS_PATH:
            raise PaperError("Untrusted ticker source")
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(ticker.fetched_at)).total_seconds()
        if not 0 <= age <= 120:
            raise PaperError("Fresh ticker required")

    def open_long(self, ticker: Ticker, stop_price: str, thesis: str,
                  risk_usdt: str) -> dict:
        self._validate_ticker(ticker)
        if not thesis.strip():
            raise PaperError("A recorded thesis is required")
        risk = positive_decimal(risk_usdt, "paper risk")
        if risk > self.max_risk:
            raise PaperError("Paper risk exceeds configured cap")
        entry = positive_decimal(ticker.ask_price, "ask") * (1 + self.slippage)
        stop = positive_decimal(stop_price, "stop")
        if stop >= entry:
            raise PaperError("Long stop must be below simulated entry")
        # Includes adverse slippage and fees in the planned loss at the stop.
        stop_fill = stop * (1 - self.slippage)
        loss_per_unit = entry - stop_fill + entry * self.fee + stop_fill * self.fee
        quantity = risk / loss_per_unit
        events = self.events()
        closed_ids = {e["id"] for e in events if e.get("event") == "close"}
        open_events = [e for e in events if e.get("event") == "open" and e["id"] not in closed_ids]
        current_risk = sum((Decimal(e["planned_risk_usdt"]) for e in open_events), Decimal(0))
        current_notional = sum((Decimal(e["quantity"]) * Decimal(e["entry"])
                                for e in open_events), Decimal(0))
        if current_risk + risk > self.max_total_open_risk:
            raise PaperError("Combined open paper risk exceeds configured cap")
        if current_notional + quantity * entry > self.equity:
            raise PaperError("Paper position exceeds unleveraged equity")
        event = {
            "event": "open", "id": str(uuid4()), "at": datetime.now(timezone.utc).isoformat(),
            "symbol": ticker.symbol, "side": "long", "thesis": thesis,
            "source": ticker.source, "snapshot_at": ticker.fetched_at,
            "entry": str(entry), "stop": str(stop), "quantity": str(quantity),
            "planned_risk_usdt": str(risk), "entry_fee_usdt": str(quantity * entry * self.fee),
            "fee_rate": str(self.fee), "slippage_rate": str(self.slippage),
        }
        self._record(event)
        return event

    def close_long(self, trade_id: str, ticker: Ticker) -> dict:
        self._validate_ticker(ticker)
        events = self.events()
        opened = next((e for e in events if e.get("event") == "open" and e.get("id") == trade_id), None)
        if not opened or opened["symbol"] != ticker.symbol:
            raise PaperError("Unknown paper trade or wrong symbol")
        if any(e.get("event") == "close" and e.get("id") == trade_id for e in events):
            raise PaperError("Paper trade is already closed")
        quantity = Decimal(opened["quantity"])
        exit_price = positive_decimal(ticker.bid_price, "bid") * (1 - self.slippage)
        exit_fee = quantity * exit_price * self.fee
        net = quantity * (exit_price - Decimal(opened["entry"])) - Decimal(opened["entry_fee_usdt"]) - exit_fee
        event = {
            "event": "close", "id": trade_id, "at": datetime.now(timezone.utc).isoformat(),
            "source": ticker.source, "snapshot_at": ticker.fetched_at,
            "exit": str(exit_price), "exit_fee_usdt": str(exit_fee), "net_pnl_usdt": str(net),
        }
        self._record(event)
        return event

    def summary(self) -> dict:
        events = self.events()
        opened = {e["id"]: e for e in events if e.get("event") == "open"}
        closed = {e["id"]: e for e in events if e.get("event") == "close"}
        return {"open_count": len(opened.keys() - closed.keys()),
                "closed_count": len(closed),
                "realized_net_pnl_usdt": str(sum(
                    (Decimal(e["net_pnl_usdt"]) for e in closed.values()), Decimal(0)))}
