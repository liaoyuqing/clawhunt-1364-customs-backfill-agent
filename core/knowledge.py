"""模块一知识库：报关品名 -> 标准编码，两步确认 + 磁盘持久化。

状态机：pending_supplier -> pending_internal -> confirmed，任意一步可 rejected。
每一步都记录确认人、时间与结论；确认结果落盘到 JSON 文件，进程重启后仍在。
知识库路径只从环境变量或构造参数来，默认落在仓库之外的用户目录。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .naming import NameIndex

STATUS_NEW = "new"
STATUS_REUSED = "reused"
STATUS_CONFLICT = "conflict"
STATUS_SKIPPED = "skipped"

CONFIRM_SUPPLIER = "supplier"
CONFIRM_INTERNAL = "internal"

"""确认流程的两个阶段。"""
STATE_PENDING_SUPPLIER = "pending_supplier"
STATE_PENDING_INTERNAL = "pending_internal"
STATE_CONFIRMED = "confirmed"
STATE_REJECTED = "rejected"

ENV_DB = "CLAWHUNT_KNOWLEDGE_DB"


def default_db_path() -> str:
    """默认库文件：环境变量优先，否则落在用户目录（仓库之外）。"""
    env_path = str(os.environ.get(ENV_DB, "")).strip()
    if env_path:
        return env_path
    return os.path.join(os.path.expanduser("~"), ".clawhunt-1364", "knowledge.json")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DeclareNameBook:
    """报关品名知识库：供应商确认 + 内部二次确认后才算沉淀完成。

    path 为空时只留在内存；给了 path 就每次改动都落盘，进程重启后仍能读回。
    """

    def __init__(self, cfg: Dict[str, Any], path: Optional[str] = None) -> None:
        self.index = NameIndex(cfg)
        self.path = str(path) if path else ""
        self.pending: List[Dict[str, str]] = []
        self.confirmed: Dict[str, Dict[str, Any]] = {}
        self.events: List[Dict[str, str]] = []
        self.entries: Dict[str, Dict[str, Any]] = {}
        self.load_errors: List[str] = []
        if self.path:
            self._load()

    # ---------- 持久化 ----------

    @property
    def persisted(self) -> bool:
        return bool(self.path)

    def _load(self) -> None:
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as handle:
                doc = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            self.load_errors.append("%s: %s" % (type(exc).__name__, exc))
            return
        for row in doc.get("entries", []):
            name = str(row.get("name", ""))
            code = str(row.get("code", ""))
            if not name or not code:
                continue
            self.entries[name] = dict(row)
            self.index.add(name, code)
            state = str(row.get("state", STATE_PENDING_SUPPLIER))
            if state == STATE_CONFIRMED:
                self.confirmed[name] = {
                    "code": code,
                    "supplier": True,
                    "internal": True,
                    "operator": str((row.get("supplier") or {}).get("operator", "")),
                    "state": state,
                }
            else:
                self.pending.append(self._pending_row(name, code, state))

    def _pending_row(self, name: str, code: str, state: str) -> Dict[str, str]:
        need = CONFIRM_SUPPLIER + "+" + CONFIRM_INTERNAL
        if state == STATE_PENDING_INTERNAL:
            need = CONFIRM_INTERNAL
        return {"name": name, "code": code, "need": need, "state": state}

    def save(self) -> bool:
        """把知识库写盘；失败只记一次，不抛。"""
        if not self.path:
            return False
        folder = os.path.dirname(self.path)
        try:
            if folder:
                os.makedirs(folder, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as handle:
                json.dump(self.to_dict(), handle, ensure_ascii=False, indent=2, sort_keys=True)
        except OSError as exc:
            self.load_errors.append("%s: %s" % (type(exc).__name__, exc))
            return False
        return True

    # ---------- 观察与确认 ----------

    def observe(
        self,
        name: str,
        code: str,
        material: str = "",
        usage: str = "",
        unit: str = "",
    ) -> str:
        """遇到一个品名：新名字入待确认，已确认过的直接复用，编码冲突只记录不覆盖。

        material / usage / unit 是这条品名的材质、用途与单位，一并落盘，
        确认后的知识条目就是「品名 -> HSCODE / 材质 / 用途 / 单位」的完整记录。
        """
        text = str(name or "").strip()
        key = str(code or "").strip()
        if not text or not key:
            self.events.append({"name": text, "code": key, "status": STATUS_SKIPPED})
            return STATUS_SKIPPED
        known = self.index.find(text)
        if known is None:
            self.index.add(text, key)
            self.entries[text] = {
                "name": text,
                "code": key,
                "material": str(material),
                "usage": str(usage),
                "unit": str(unit),
                "state": STATE_PENDING_SUPPLIER,
                "supplier": {},
                "internal": {},
                "updated_at": _now(),
            }
            self.pending.append(self._pending_row(text, key, STATE_PENDING_SUPPLIER))
            self.events.append({"name": text, "code": key, "status": STATUS_NEW})
            self.save()
            return STATUS_NEW
        if known == key:
            self.events.append({"name": text, "code": key, "status": STATUS_REUSED})
            return STATUS_REUSED
        self.index.add(text, key)
        self.events.append({"name": text, "code": key, "status": STATUS_CONFLICT})
        return STATUS_CONFLICT

    def _entry(self, name: str) -> Optional[Dict[str, Any]]:
        return self.entries.get(str(name or "").strip())

    def state_of(self, name: str) -> str:
        entry = self._entry(name)
        if entry is None:
            return ""
        return str(entry.get("state", ""))

    def confirm_supplier(self, name: str, operator: str = "", note: str = "", at: str = "") -> bool:
        """供应商确认：pending_supplier -> pending_internal，仍需内部二次确认。"""
        entry = self._entry(name)
        if entry is None:
            return False
        if str(entry.get("state")) == STATE_CONFIRMED:
            return True
        entry["supplier"] = {"operator": str(operator), "at": at or _now(), "note": str(note)}
        entry["state"] = STATE_PENDING_INTERNAL
        entry["updated_at"] = _now()
        self._sync_pending(str(entry["name"]), str(entry["code"]), STATE_PENDING_INTERNAL)
        self.save()
        return False

    def confirm_internal(self, name: str, operator: str = "", note: str = "", at: str = "") -> bool:
        """内部二次确认：pending_internal -> confirmed，两步齐了才算沉淀完成。"""
        entry = self._entry(name)
        if entry is None:
            return False
        if not (entry.get("supplier") or {}).get("operator"):
            """供应商确认没到位时，内部确认不生效。"""
            return False
        entry["internal"] = {"operator": str(operator), "at": at or _now(), "note": str(note)}
        entry["state"] = STATE_CONFIRMED
        entry["updated_at"] = _now()
        key = str(entry["name"])
        self.confirmed[key] = {
            "code": str(entry["code"]),
            "supplier": True,
            "internal": True,
            "operator": str(operator),
            "state": STATE_CONFIRMED,
        }
        self.pending = [item for item in self.pending if item["name"] != key]
        self.save()
        return True

    def reject(self, name: str, operator: str = "", note: str = "", at: str = "") -> bool:
        """任一步都可拒绝；被拒的品名不得写成台账确认编码。"""
        entry = self._entry(name)
        if entry is None:
            return False
        entry["state"] = STATE_REJECTED
        entry["rejected_by"] = str(operator)
        entry["rejected_at"] = at or _now()
        entry["reject_note"] = str(note)
        entry["updated_at"] = _now()
        key = str(entry["name"])
        self.confirmed.pop(key, None)
        self.pending = [item for item in self.pending if item["name"] != key]
        self.save()
        return True

    def _sync_pending(self, name: str, code: str, state: str) -> None:
        for item in self.pending:
            if item["name"] == name:
                item["need"] = CONFIRM_INTERNAL if state == STATE_PENDING_INTERNAL else CONFIRM_SUPPLIER + "+" + CONFIRM_INTERNAL
                item["state"] = state
                return
        self.pending.append(self._pending_row(name, code, state))

    def confirm(self, name: str, supplier_ok: bool, internal_ok: bool, operator: str = "") -> bool:
        """两次确认都到位才把条目从待确认挪进已确认；任一缺失保持待确认。"""
        code = self.index.find(name)
        if code is None:
            return False
        if supplier_ok:
            self.confirm_supplier(name, operator=operator)
        if internal_ok:
            return self.confirm_internal(name, operator=operator)
        row = self.confirmed.get(str(name))
        if row is not None and row.get("supplier") and row.get("internal"):
            return True
        return False

    def resolve(self, name: str) -> Optional[str]:
        return self.index.find(name)

    def confirmed_codes(self) -> Dict[str, str]:
        """只有走到 confirmed 的品名才有资格写成台账确认编码。"""
        return {name: str(row.get("code", "")) for name, row in self.confirmed.items()}

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
            "entries": [self.entries[name] for name in sorted(self.entries)],
        }