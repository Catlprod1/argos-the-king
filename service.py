from __future__ import annotations

from .approvals import ApprovalQueue
from .audit import AuditLog
from .config import Config
from .models import Action, Decision, PolicyResult
from .policy import PolicyGateway


class ArgosService:
    def __init__(self, config: Config):
        self.config = config
        self.audit = AuditLog(config.data_directory / "audit.jsonl")
        self.approvals = ApprovalQueue(config.data_directory / "approvals.json")
        self.policy = PolicyGateway(config)

    def propose(self, action: Action) -> PolicyResult:
        result = self.policy.evaluate(action)
        self.audit.append("policy_evaluation", {"action": action.to_dict(), "result": result.to_dict()})
        if result.decision == Decision.APPROVAL_REQUIRED:
            self.approvals.add(action, result)
        return result
