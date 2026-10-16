from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskLimits:
    equity_aud: float
    per_trade_percent: float
    total_open_percent: float
    soft_daily_percent: float
    hard_daily_percent: float
    max_leverage: float

    @property
    def per_trade_aud(self) -> float:
        return self.equity_aud * self.per_trade_percent / 100

    @property
    def total_open_aud(self) -> float:
        return self.equity_aud * self.total_open_percent / 100

    @property
    def soft_daily_aud(self) -> float:
        return self.equity_aud * self.soft_daily_percent / 100

    @property
    def hard_daily_aud(self) -> float:
        return self.equity_aud * self.hard_daily_percent / 100

    @classmethod
    def from_trading_config(cls, trading: dict) -> "RiskLimits":
        return cls(
            equity_aud=float(trading.get("learning_equity_aud", 0)),
            per_trade_percent=float(trading.get("max_risk_per_trade_percent", 0)),
            total_open_percent=float(trading.get("max_total_open_risk_percent", 0)),
            soft_daily_percent=float(trading.get("soft_daily_loss_percent", 0)),
            hard_daily_percent=float(trading.get("hard_daily_loss_percent", 0)),
            max_leverage=float(trading.get("max_leverage", 1)),
        )

    def position_notional_aud(self, stop_distance_percent: float) -> float:
        if stop_distance_percent <= 0:
            raise ValueError("Stop distance must be positive")
        risk_sized = self.per_trade_aud / (stop_distance_percent / 100)
        leverage_cap = self.equity_aud * self.max_leverage
        return min(risk_sized, leverage_cap)
