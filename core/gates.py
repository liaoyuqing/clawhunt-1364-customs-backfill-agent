"""人工确认闸门：任何对外的开票/提交动作都要在此阻塞，直到有人确认。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional


class HumanApprovalRequired(RuntimeError):
    """未获人工确认就尝试对外提交。"""

    def __init__(self, action: str, digest: str) -> None:
        super().__init__("human approval required for %s (payload %s)" % (action, digest))
        self.action = action
        self.digest = digest


def payload_digest(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


@dataclass(frozen=True)
class Approval:
    action: str
    operator: str
    digest: str
    at: str

    def as_audit(self) -> Dict[str, str]:
        return {"action": self.action, "operator": self.operator, "digest": self.digest, "at": self.at}


class AuditLog:
    """审计行：操作人、动作、时间、载荷摘要。"""

    def __init__(self) -> None:
        self.entries: List[Dict[str, str]] = []

    def append(self, approval: Approval, batch_no: str = "") -> None:
        row = approval.as_audit()
        row["batch_no"] = batch_no
        self.entries.append(row)

    def by_batch(self, batch_no: str) -> List[Dict[str, str]]:
        return [e for e in self.entries if e.get("batch_no") == batch_no]


class HumanGate:
    """确认来源可注入：无人值守时 approver 返回 None 即阻塞。"""

    def __init__(
        self,
        approver: Optional[Callable[[str, Dict[str, Any]], Optional[str]]] = None,
        audit: Optional[AuditLog] = None,
        clock: Optional[Callable[[], str]] = None,
    ) -> None:
        self._approver = approver
        self.audit = audit if audit is not None else AuditLog()
        self._clock = clock or (lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
        self.required_actions: List[str] = []

    def require_human(self, action: str, payload: Dict[str, Any], batch_no: str = "") -> Approval:
        """没有确认就抛错，绝不静默放行。"""
        digest = payload_digest(payload)
        self.required_actions.append(action)
        operator = self._approver(action, payload) if self._approver is not None else None
        if not operator:
            raise HumanApprovalRequired(action, digest)
        approval = Approval(action=action, operator=str(operator), digest=digest, at=self._clock())
        self.audit.append(approval, batch_no=batch_no)
        return approval