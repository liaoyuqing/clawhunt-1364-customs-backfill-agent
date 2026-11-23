"""买家退返第 5 条：电子税务局半自动填单，人工核对通过才允许提交。

未确认时提交调用次数必须是 0；确认后必须是 1，且请求体含发票号与税号。
"""

from __future__ import annotations

import json

from core.contract import Contract, ContractLine, build_contract
from core.etax import (
    ACTION_SUBMIT,
    EtaxClient,
    EtaxConfig,
    build_draft,
    pending_submissions,
    submit_with_gate,
)
from core.gates import AuditLog, HumanGate
from core.http_client import HttpResponse, RecordingTransport

CONTRACT_NO = "HT-2026-0001"
INVOICE_NO = "INV-SYNTHETIC-0001"
BUYER_NAME = "深圳前海跨境电子商务有限公司"
BUYER_TAX_NO = "91440300MA5F0000XA"


def _contract():
    lines = [
        ContractLine(sku="SKU-1004", product_name="礼品卡套", invoice_name="礼品卡套", declare_name="礼品卡套", spec="型号 M-1004", unit="个", qty="3", unit_price="3.20"),
        ContractLine(sku="SKU-1001", product_name="桌面收纳盒", invoice_name="桌面收纳盒", declare_name="桌面收纳盒", spec="型号 M-1001", unit="个", qty="8", unit_price="12.80"),
    ]
    return build_contract(CONTRACT_NO, "东莞文具制造有限公司", BUYER_NAME, lines)


def _invoice_view():
    return {
        "invoice_no": INVOICE_NO,
        "buyer_name": BUYER_NAME,
        "buyer_tax_no": BUYER_TAX_NO,
        "invoice_type": "增值税专用发票",
        "amount": "112.00",
        "issued_at": "2026-11-20",
    }


def _draft():
    return build_draft("B2026-0001", _contract(), _invoice_view())


def _client(transport):
    return EtaxClient(EtaxConfig(base_url="https://etax.example.invalid", token="TEST-ONLY-NOT-REAL"), http=transport)


def _ok_response():
    body = json.dumps({"code": 0, "submit_no": "SUBMIT-SYNTHETIC-0001"}).encode("utf-8")
    return HttpResponse(200, {"Content-Type": "application/json"}, body)


def test_draft_maps_contract_and_invoice_fields():
    draft = _draft()
    assert draft["batch_no"] == "B2026-0001"
    assert draft["contract_no"] == CONTRACT_NO
    assert draft["invoice_no"] == INVOICE_NO
    assert draft["buyer_name"] == BUYER_NAME
    assert draft["buyer_tax_no"] == BUYER_TAX_NO
    assert draft["invoice_type"] == "增值税专用发票"
    assert draft["amount"] == "112.00"
    assert len(draft["items"]) == 2
    assert draft["items"][0]["sku"] == "SKU-1004"
    assert draft["items"][0]["amount"] == "9.60"
    assert draft["items"][1]["amount"] == "102.40"


def test_without_manual_confirmation_nothing_is_submitted():
    """人工确认返回空串 / None / 闸门未启用：提交次数一律为 0。"""
    for approver in (lambda action, payload: "", lambda action, payload: None, None):
        transport = RecordingTransport([_ok_response()])
        audit = AuditLog(path="", persist=False)
        gate = HumanGate(approver=approver, audit=audit)
        answer = submit_with_gate(gate, _client(transport), _draft(), batch_no="B2026-0001")
        assert answer["submitted"] is False
        assert len(transport.requests) == 0, "未确认时不得发出任何提交请求"


def test_after_confirmation_exactly_one_request_is_sent():
    transport = RecordingTransport([_ok_response()])
    audit = AuditLog(path="", persist=False)
    gate = HumanGate(approver=lambda action, payload: "张审核", audit=audit, clock=lambda: "2026-11-23T10:00:00+08:00")
    answer = submit_with_gate(gate, _client(transport), _draft(), batch_no="B2026-0001")
    assert answer["submitted"] is True
    assert answer["submit_no"] == "SUBMIT-SYNTHETIC-0001"
    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.method == "POST"
    assert request.url == "https://etax.example.invalid/v1/declaration/submit"
    assert "x-etax-access-token" in request.headers
    assert request.describe()["headers"]["x-etax-access-token"] == "***"
    payload = request.body_json()
    assert payload["invoice_no"] == INVOICE_NO
    assert payload["buyer_tax_no"] == BUYER_TAX_NO


def test_audit_row_records_operator_and_batch(tmp_path):
    import os

    path = os.path.join(str(tmp_path), "logs", "audit.log")
    audit = AuditLog(path=path)
    gate = HumanGate(approver=lambda action, payload: "李复核", audit=audit, clock=lambda: "2026-11-23T10:00:00+08:00")
    transport = RecordingTransport([_ok_response()])
    submit_with_gate(gate, _client(transport), _draft(), batch_no="B2026-0001")
    rows = audit.read_back()
    assert len(rows) == 1
    assert rows[0]["operator"] == "李复核"
    assert rows[0]["batch_no"] == "B2026-0001"
    assert rows[0]["action"] == ACTION_SUBMIT


def test_without_credentials_only_draft_and_pending_list():
    """没有凭据时只产出草稿与待提交清单，不假装提交。"""
    transport = RecordingTransport([])
    audit = AuditLog(path="", persist=False)
    gate = HumanGate(approver=lambda action, payload: "张审核", audit=audit)
    answer = submit_with_gate(gate, None, _draft(), batch_no="B2026-0001")
    assert answer["submitted"] is False
    assert "没有可用的电子税务局客户端" in answer["reason"]
    assert len(transport.requests) == 0
    pending = pending_submissions([_draft()])
    assert pending[0]["batch_no"] == "B2026-0001"
    assert pending[0]["invoice_no"] == INVOICE_NO
    assert pending[0]["status"] == "pending_manual_submit"
