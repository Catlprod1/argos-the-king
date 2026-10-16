from __future__ import annotations

import math
from urllib.parse import urlparse

from .config import Config
from .models import Action, Decision, PolicyResult
from .risk import RiskLimits


PROHIBITED_ACTIONS = {
    "fund_account",
    "deposit",
    "purchase",
    "borrow",
    "withdraw",
    "external_transfer",
    "change_withdrawal_address",
    "change_password",
    "change_mfa",
    "change_recovery",
    "change_permissions",
    "create_api_key",
    "training_assessment_submit",
    "training_declaration",
    "identity_verification",
}

APPROVAL_ACTIONS = {
    "send_email",
    "send_message",
    "submit_form",
    "upload_certificate",
    "place_trade",
    "amend_trade",
    "cancel_trade",
}

READ_ONLY_ACTIONS = {
    "read_email",
    "read_market_data",
    "read_training_status",
    "index_certificate",
    "draft_email",
    "draft_equivalency_request",
    "paper_trade",
    "generate_brief",
    "generate_alert",
}


class PolicyGateway:
    def __init__(self, config: Config):
        self.config = config

    def evaluate(self, action: Action) -> PolicyResult:
        if action.kind in PROHIBITED_ACTIONS:
            return PolicyResult(Decision.DENY, "Prohibited by immutable charter", action.id)

        access_error = self._check_access(action)
        if access_error:
            return PolicyResult(Decision.DENY, access_error, action.id)

        if action.kind in {"place_trade", "amend_trade", "cancel_trade"}:
            trading = self.config.section("trading")
            if not trading.get("enabled", False):
                return PolicyResult(Decision.DENY, "Trading is disabled", action.id)
            # This MVP has no production trading authority, even if a config file
            # is accidentally changed. Require the exact testnet API endpoint.
            if (not trading.get("testnet_only", True)
                    or action.payload.get("testnet") is not True
                    or action.target != "https://api-testnet.bybit.com"
                    or not action.account.startswith("bybit:testnet")
                    or action.account not in self.config.section("access").get("allowed_accounts", [])):
                return PolicyResult(Decision.DENY, "Only allow-listed testnet trading is enabled", action.id)
            if action.kind == "cancel_trade":
                return PolicyResult(Decision.APPROVAL_REQUIRED, "Exact testnet cancellation requires owner approval", action.id)
            if trading.get("require_stop_loss", True) and action.kind == "place_trade":
                try:
                    stop_loss = float(action.payload["stop_loss"])
                except (KeyError, TypeError, ValueError, OverflowError):
                    return PolicyResult(Decision.DENY, "A stop loss is mandatory", action.id)
                if not math.isfinite(stop_loss) or stop_loss <= 0:
                    return PolicyResult(Decision.DENY, "A valid stop loss is mandatory", action.id)
            limits = RiskLimits.from_trading_config(trading)
            try:
                planned_risk = float(action.payload["planned_risk_aud"])
                open_risk = float(action.payload["current_open_risk_aud"])
                leverage = float(action.payload.get("leverage", 1))
                daily_loss = float(action.payload["daily_loss_percent"])
            except (KeyError, TypeError, ValueError, OverflowError):
                return PolicyResult(Decision.DENY, "Valid risk measurements are required", action.id)
            if (not all(math.isfinite(value) for value in
                        (planned_risk, open_risk, leverage, daily_loss))
                    or planned_risk <= 0 or open_risk < 0
                    or leverage < 1 or daily_loss < 0):
                return PolicyResult(Decision.DENY, "Risk measurements are out of range", action.id)
            if planned_risk > limits.per_trade_aud:
                return PolicyResult(Decision.DENY, "Planned trade risk exceeds per-trade limit", action.id)
            if open_risk + planned_risk > limits.total_open_aud:
                return PolicyResult(Decision.DENY, "Combined open risk exceeds configured limit", action.id)
            if leverage > float(trading.get("max_leverage", 1)):
                return PolicyResult(Decision.DENY, "Leverage exceeds configured maximum", action.id)
            if daily_loss >= float(trading.get("hard_daily_loss_percent", 0)):
                return PolicyResult(Decision.DENY, "Hard daily-loss stop has been reached", action.id)
            if daily_loss >= float(trading.get("soft_daily_loss_percent", 0)):
                return PolicyResult(Decision.APPROVAL_REQUIRED, "Soft daily-loss stop requires owner review", action.id)

        if action.kind in APPROVAL_ACTIONS:
            return PolicyResult(
                Decision.APPROVAL_REQUIRED,
                "Exact action requires owner approval",
                action.id,
            )
        if action.kind in READ_ONLY_ACTIONS:
            return PolicyResult(Decision.ALLOW, "Read-only or local action", action.id)
        return PolicyResult(Decision.DENY, "Unknown actions default to deny", action.id)

    def _check_access(self, action: Action) -> str | None:
        access = self.config.section("access")
        allowed_accounts = set(access.get("allowed_accounts", []))
        if action.account and action.account not in allowed_accounts:
            return f"Account is not allow-listed: {action.account}"

        if action.target.startswith(("http://", "https://")):
            hostname = (urlparse(action.target).hostname or "").lower()
            allowed_domains = {d.lower() for d in access.get("allowed_domains", [])}
            if hostname not in allowed_domains:
                return f"Domain is not allow-listed: {hostname}"
        return None
