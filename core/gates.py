"""人工确认闸门：任何对外的开票/提交动作都要在此阻塞，直到有人确认。"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

"""审计落盘默认路径；可用构造参数或环境变量覆盖，写空串即只留进程内。"""
DEFAULT_AUDIT_PATH = os.path.join(ROOT, "logs", "audit.log")


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
    """审计行：操作人、动作、时间、载荷摘要；每行同时追加到 JSONL 文件，便于事后检索。

    落盘是尽力而为：目录建不出来或磁盘只读时不影响闸门判定，只记一次写入失败，
    调用方可用 ``persisted`` / ``write_failures`` 判断审计是否真的落地。
    """

    def __init__(self, path: Optional[str] = None, persist: bool = True) -> None:
        self.path = str(path if path is not None else DEFAULT_AUDIT_PATH)
        self.persist = bool(persist) and bool(self.path)
        self.entries: List[Dict[str, str]] = []
        self.write_failures: List[str] = []

    def append(self, approval: Approval, batch_no: str = "") -> None:
        row = approval.as_audit()
        row["batch_no"] = batch_no
        self.entries.append(row)
        self._flush(row)

    def _flush(self, row: Dict[str, str]) -> None:
        if not self.persist:
            return
        try:
            folder = os.path.dirname(self.path)
            if folder:
                os.makedirs(folder, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError as exc:
            self.write_failures.append("%s: %s" % (type(exc).__name__, exc))

    @property
    def persisted(self) -> bool:
        return self.persist and not self.write_failures

    def read_back(self) -> List[Dict[str, str]]:
        """按落盘文件读回审计行，测试与运维检索都走这里。"""
        if not self.path or not os.path.exists(self.path):
            return []
        out: List[Dict[str, str]] = []
        with open(self.path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def query(
        self,
        batch_no: Optional[str] = None,
        action: Optional[str] = None,
        operator: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """按批次号 / 动作 / 操作人检索**落盘**的审计行。

        每次都从审计文件重新读，所以进程重启后（新 AuditLog 实例）依然查得到历史。
        没有任何过滤条件时返回全部落盘行；文件不存在返回空列表。
        """
        rows = self.read_back()
        out: List[Dict[str, str]] = []
        for row in rows:
            if batch_no is not None and str(row.get("batch_no", "")) != str(batch_no):
                continue
            if action is not None and str(row.get("action", "")) != str(action):
                continue
            if operator is not None and str(row.get("operator", "")) != str(operator):
                continue
            out.append(row)
        return out

    def query_by_batch(self, batch_no: str) -> List[Dict[str, str]]:
        """只按批次号检索（落盘口径）。"""
        return self.query(batch_no=batch_no)

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