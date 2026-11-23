"""AC5：涉及税局的提交必须人工确认；无确认零提交，有确认一次并留审计。"""

from __future__ import annotations

import pytest

from core.gates import AuditLog, HumanApprovalRequired, HumanGate, payload_digest


class TaxBureauStub:
    """出网桩：只数被调用了几次。"""

    def __init__(self) -> None:
        self.calls = 0

    def submit(self, payload):
        self.calls += 1
        return {"ok": True}


def _clock():
    return "2026-11-23T10:00:00+08:00"


def test_ac5_without_approval_zero_submissions():
    stub = TaxBureauStub()
    gate = HumanGate(approver=None, clock=_clock)
    with pytest.raises(HumanApprovalRequired) as exc:
        gate.require_human("tax_bureau_submit", {"batch_no": "B2026-0001"})
        stub.submit({"batch_no": "B2026-0001"})
    assert stub.calls == 0
    assert exc.value.action == "tax_bureau_submit"
    assert gate.audit.entries == []


def test_ac5_approver_returning_empty_string_still_blocks():
    gate = HumanGate(approver=lambda action, payload: "", clock=_clock)
    with pytest.raises(HumanApprovalRequired):
        gate.require_human("tax_bureau_submit", {"batch_no": "B2026-0001"})


def test_ac5_with_approval_one_submission_and_audit_row():
    stub = TaxBureauStub()
    gate = HumanGate(approver=lambda action, payload: "bobo", clock=_clock)
    payload = {"batch_no": "B2026-0002", "invoice_no": "INV-2002"}
    approval = gate.require_human("tax_bureau_submit", payload, batch_no="B2026-0002")
    stub.submit(payload)
    assert stub.calls == 1
    assert approval.operator == "bobo"
    assert approval.digest == payload_digest(payload)
    rows = gate.audit.by_batch("B2026-0002")
    assert len(rows) == 1
    assert set(rows[0]) == {"action", "operator", "digest", "at", "batch_no"}
    assert rows[0]["at"] == "2026-11-23T10:00:00+08:00"
    assert len(rows[0]["digest"]) == 8


def test_ac5_audit_log_searchable_by_batch():
    log = AuditLog()
    gate = HumanGate(approver=lambda a, p: "ops", audit=log, clock=_clock)
    gate.require_human("tax_bureau_submit", {"n": 1}, batch_no="B1")
    gate.require_human("tax_bureau_submit", {"n": 2}, batch_no="B2")
    assert len(log.by_batch("B1")) == 1
    assert len(log.by_batch("B2")) == 1
    assert log.by_batch("B3") == []
    assert gate.required_actions == ["tax_bureau_submit", "tax_bureau_submit"]