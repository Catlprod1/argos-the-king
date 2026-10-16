from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .approvals import ApprovalQueue
from .audit import AuditLog
from .config import Config
from .training import TrainingRegister
from .whalebot import digest


class BriefingService:
    def __init__(
        self,
        config: Config,
        audit: AuditLog,
        approvals: ApprovalQueue,
        training: TrainingRegister,
    ):
        self.config = config
        self.audit = audit
        self.approvals = approvals
        self.training = training

    def render(self) -> str:
        owner = self.config.section("owner")
        tz = ZoneInfo(owner.get("timezone", "Australia/Perth"))
        now = datetime.now(tz)
        pending = self.approvals.pending()
        records = self.training.list()
        recent = self.audit.recent(5)
        trading = self.config.section("trading")

        lines = [
            f"Argos morning brief — {now:%A %d %B %Y, %H:%M}",
            f"Owner: {owner.get('display_name', 'Owner')}",
            "",
            f"Pending approvals: {len(pending)}",
            f"Training records indexed: {len(records)}",
            f"Trading: {'enabled' if trading.get('enabled') else 'disabled'} "
            f"({'testnet only' if trading.get('testnet_only', True) else 'mainnet permitted'})",
            f"Recent audited events: {len(recent)}",
        ]
        if pending:
            lines.append("\nApproval queue:")
            for item in pending[:5]:
                action = item["action"]
                lines.append(f"- {action['kind']} → {action.get('target') or action.get('account')} [{action['id']}]")
        if (self.config.data_directory / "whalebot.json").exists():
            lines.append("\n" + digest(self.config.data_directory / "whalebot.json"))
        lines.append("\nArgos is watching. No prohibited actions can be approved.")
        return "\n".join(lines)
