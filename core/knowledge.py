"""模块一知识库沉淀：品名 -> 标准编码，新名字先挂待确认，确认后复用。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .naming import NameIndex

STATUS_NEW = "new"
STATUS_REUSED = "reused"
STATUS_CONFLICT = "conflict"
STATUS_SKIPPED = "skipped"

CONFIRM_SUPPLIER = "supplier"
CONFIRM_INTERNAL = "internal"


class DeclareNameBook:
    """报关品名知识库：供应商确认 + 内部二次确认后才算沉淀完成。"""

    def __init__(self, cfg: Dict[str, Any]) -> None:
        self.index = NameIndex(cfg)
        self.pending: List[Dict[str, str]] = []
        self.confirmed: Dict[str, Dict[str, Any]] = {}
        self.events: List[Dict[str, str]] = []

    def observe(self, name: str, code: str) -> str:
        """遇到一个品名：新名字入待确认，已确认过的直接复用，编码冲突只记录不覆盖。"""
        text = str(name or "").strip()
        key = str(code or "").strip()
        if not text or not key:
            self.events.append({"name": text, "code": key, "status": STATUS_SKIPPED})
            return STATUS_SKIPPED
        known = self.index.find(text)
        if known is None:
            self.index.add(text, key)
            self.pending.append(
                {
                    "name": text,
                    "code": key,
                    "need": "%s+%s" % (CONFIRM_SUPPLIER, CONFIRM_INTERNAL),
                }
            )
            self.events.append({"name": text, "code": key, "status": STATUS_NEW})
            return STATUS_NEW
        if known == key:
            self.events.append({"name": text, "code": key, "status": STATUS_REUSED})
            return STATUS_REUSED
        self.index.add(text, key)
        self.events.append({"name": text, "code": key, "status": STATUS_CONFLICT})
        return STATUS_CONFLICT

    def confirm(self, name: str, supplier_ok: bool, internal_ok: bool, operator: str = "") -> bool:
        """两次确认都到位才把条目从待确认挪进已确认；任一缺失保持待确认。"""
        code = self.index.find(name)
        if code is None:
            return False
        row = self.confirmed.setdefault(
            name,
            {"code": code, "supplier": False, "internal": False, "operator": ""},
        )
        if supplier_ok:
            row["supplier"] = True
        if internal_ok:
            row["internal"] = True
        if operator:
            row["operator"] = str(operator)
        done = bool(row["supplier"] and row["internal"])
        if done:
            self.pending = [item for item in self.pending if item["name"] != name]
        return done

    def resolve(self, name: str) -> Optional[str]:
        return self.index.find(name)

    def status_counts(self) -> Dict[str, int]:
        out = {STATUS_NEW: 0, STATUS_REUSED: 0, STATUS_CONFLICT: 0, STATUS_SKIPPED: 0}
        for event in self.events:
            out[event["status"]] = out.get(event["status"], 0) + 1
        return out

    def to_dict(self) -> Dict[str, Any]:
        counts = self.status_counts()
        return {
            "distinct_names": len(self.index),
            "observations": len(self.events),
            "new": counts[STATUS_NEW],
            "reused": counts[STATUS_REUSED],
            "conflicts": counts[STATUS_CONFLICT],
            "skipped": counts[STATUS_SKIPPED],
            "pending_confirmations": len(self.pending),
            "confirmed": len(self.confirmed),
            "conflict_rows": [
                {"left": left, "right": right, "name": name} for name, left, right in self.index.conflicts
            ],
        }