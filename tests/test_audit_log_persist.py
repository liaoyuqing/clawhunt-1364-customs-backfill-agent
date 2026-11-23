"""AC5 补充：审计真的落盘成 JSONL，可按批次检索；落盘失败不影响闸门判定。"""

from __future__ import annotations

import json
import os

from core.gates import AuditLog, HumanGate


def _gate(tmp_path, name="audit.log"):
    path = os.path.join(str(tmp_path), "logs", name)
    log = AuditLog(path=path)
    gate = HumanGate(approver=lambda action, payload: "ops", audit=log, clock=lambda: "2026-11-23T10:00:00+08:00")
    return gate, log, path


def test_audit_log_writes_jsonl_to_default_path(tmp_path):
    gate, log, path = _gate(tmp_path)
    gate.require_human("tax_bureau_submit", {"n": 1}, batch_no="B1")
    gate.require_human("tax_bureau_submit", {"n": 2}, batch_no="B2")
    assert os.path.exists(path)
    assert log.persisted is True
    with open(path, encoding="utf-8") as handle:
        lines = [json.loads(line) for line in handle if line.strip()]
    assert len(lines) == 2
    assert [row["batch_no"] for row in lines] == ["B1", "B2"]
    assert all(set(row) == {"action", "operator", "digest", "at", "batch_no"} for row in lines)
    assert lines[0]["operator"] == "ops"
    assert len(lines[0]["digest"]) == 8


def test_audit_log_read_back_matches_in_memory(tmp_path):
    gate, log, _path = _gate(tmp_path)
    gate.require_human("tax_bureau_submit", {"n": 7}, batch_no="B7")
    assert log.read_back() == log.entries


def test_audit_log_can_be_disabled(tmp_path):
    path = os.path.join(str(tmp_path), "logs", "audit.log")
    log = AuditLog(path=path, persist=False)
    gate = HumanGate(approver=lambda a, p: "ops", audit=log, clock=lambda: "t")
    gate.require_human("tax_bureau_submit", {"n": 1}, batch_no="B1")
    assert log.entries and not os.path.exists(path)
    assert log.persisted is False
    assert log.read_back() == []


def test_audit_log_write_failure_does_not_block_gate(tmp_path):
    """目录位置被同名文件占住时落盘失败，但闸门仍然放行，失败记录在 write_failures 里。"""
    blocker = os.path.join(str(tmp_path), "logs")
    with open(blocker, "w", encoding="utf-8") as handle:
        handle.write("not a directory")
    log = AuditLog(path=os.path.join(blocker, "audit.log"))
    gate = HumanGate(approver=lambda a, p: "ops", audit=log, clock=lambda: "t")
    approval = gate.require_human("tax_bureau_submit", {"n": 1}, batch_no="B1")
    assert approval.operator == "ops"
    assert len(log.entries) == 1
    assert log.write_failures
    assert log.persisted is False


def test_audit_log_default_path_is_repo_logs_dir():
    from core.gates import DEFAULT_AUDIT_PATH

    assert DEFAULT_AUDIT_PATH.endswith(os.path.join("logs", "audit.log"))
    assert os.path.isabs(DEFAULT_AUDIT_PATH)