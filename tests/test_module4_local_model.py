"""买家退返第 3 条：模块四必须真实调用本地模型，且地址必须过内网校验。

证据落在「真的发出了 HTTP 请求」这一层；公网地址被 preflight 拦下时一次请求都不发。
"""

from __future__ import annotations

import json
import os

from core.http_client import HttpResponse, RecordingTransport
from core.ledger import Ledger
from core.local_model import (
    LocalModelBlocked,
    LocalModelCallError,
    LocalModelClient,
    LocalModelConfig,
    assert_local_allowed,
    parse_invoice,
)
from core.models import SourceRef
from core.module4 import InvoiceLedger, Module4Filler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECORDED = os.path.join(ROOT, "fixtures", "recorded", "local_model_invoice.json")

INVOICES = [
    {
        "category": "礼品",
        "invoice_no": "INV-2026-GIFT-01",
        "invoiced_qty": "150.00",
        "issued_at": "2026-11-04",
        "invoice_type": "增值税专用发票",
        "unit": "个",
    }
]


def _response(content):
    body = json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]}).encode("utf-8")
    return HttpResponse(200, {"Content-Type": "application/json"}, body)


def test_local_model_sends_real_http_request():
    transport = RecordingTransport([_response(json.dumps({
        "invoice_no": "INV-LOCAL-SYNTHETIC-0001",
        "issued_at": "2026-11-20",
        "buyer_name": "深圳前海跨境电子商务有限公司",
        "buyer_tax_no": "91440300MA5F0000XA",
        "amount": "9.60",
        "invoice_type": "增值税专用发票",
    }))])
    client = LocalModelClient(LocalModelConfig(base_url="http://127.0.0.1:8000"), http=transport)
    fields = client.recognize_invoice("data:image/png;base64,AAAA")
    assert fields["invoice_no"] == "INV-LOCAL-SYNTHETIC-0001"
    assert fields["buyer_tax_no"] == "91440300MA5F0000XA"
    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.method == "POST"
    assert request.url == "http://127.0.0.1:8000/v1/chat/completions"
    payload = request.body_json()
    assert payload["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_public_address_is_blocked_and_no_request_is_sent():
    """公网地址必须被 preflight 拦下，且一次请求都不发。"""
    transport = RecordingTransport([])
    client = LocalModelClient(LocalModelConfig(base_url="http://93.184.216.34:8000"), http=transport)
    try:
        client.recognize_invoice("data:image/png;base64,AAAA")
    except LocalModelBlocked as exc:
        assert "not a local address" in str(exc)
    else:
        raise AssertionError("公网地址必须被拦下")
    assert len(transport.requests) == 0, "被拦下时不得发出任何请求"


def test_private_and_loopback_addresses_are_allowed():
    for base_url in ("http://127.0.0.1:8000", "http://10.1.2.3:8000", "http://192.168.1.10:8000", "http://172.20.0.5:8000"):
        assert_local_allowed(LocalModelConfig(base_url=base_url))
    try:
        assert_local_allowed(LocalModelConfig(base_url="http://172.32.0.5:8000"))
    except LocalModelBlocked:
        pass
    else:
        raise AssertionError("172.32/12 以上不是私有段，必须拒绝")


def test_egress_whitelist_public_entry_blocks_call():
    config = LocalModelConfig(base_url="http://127.0.0.1:8000")
    try:
        assert_local_allowed(config, egress_whitelist=["10.20.0.0/16", "8.8.8.8/32"])
    except LocalModelBlocked as exc:
        assert "8.8.8.8/32" in str(exc)
    else:
        raise AssertionError("白名单里有公网地址必须拒绝")


def test_parse_invoice_rejects_missing_fields():
    try:
        parse_invoice(json.dumps({"invoice_no": "INV-1"}))
    except LocalModelCallError as exc:
        assert "缺字段" in str(exc)
    else:
        raise AssertionError("字段不全必须报错")


def test_module4_writes_matching_invoice_and_reports_mismatch(rules):
    """接上本地模型后：号码一致才写台账；不一致时报问题且发票号留空。"""
    ledger = Ledger(rules.columns)
    ledger.ensure_row("B1#0", "B1")
    ledger.write("B1#0", "SKU", "SKU-1004", SourceRef(kind="api", ref="lingxing/products", at="2026-11-23T00:00:00+08:00"))

    good = RecordingTransport([_response(json.dumps({
        "invoice_no": "INV-2026-GIFT-01",
        "issued_at": "2026-11-04",
        "buyer_name": "深圳前海跨境电子商务有限公司",
        "buyer_tax_no": "91440300MA5F0000XA",
        "amount": "23.00",
        "invoice_type": "增值税专用发票",
    }))])
    filler = Module4Filler(rules, InvoiceLedger(INVOICES), local_model=LocalModelClient(LocalModelConfig(base_url="http://127.0.0.1:8000"), http=good))
    out = filler.recognize_invoices(ledger, [{"row_key": "B1#0", "category": "礼品", "image_url": "data:image/png;base64,AAAA"}])
    assert out[0]["matched"] is True
    assert ledger.row("B1#0").value("发票号") == "INV-2026-GIFT-01"

    bad = RecordingTransport([_response(json.dumps({
        "invoice_no": "INV-DIFFERENT-SYNTHETIC-0002",
        "issued_at": "2026-11-04",
        "buyer_name": "深圳前海跨境电子商务有限公司",
        "buyer_tax_no": "91440300MA5F0000XA",
        "amount": "23.00",
        "invoice_type": "增值税专用发票",
    }))])
    filler2 = Module4Filler(rules, InvoiceLedger(INVOICES), local_model=LocalModelClient(LocalModelConfig(base_url="http://127.0.0.1:8000"), http=bad))
    ledger2 = Ledger(rules.columns)
    ledger2.ensure_row("B2#0", "B2")
    out2 = filler2.recognize_invoices(ledger2, [{"row_key": "B2#0", "category": "礼品", "image_url": "data:image/png;base64,BBBB"}])
    assert out2[0]["matched"] is False
    codes = [issue.code for issue in filler2._issues]
    assert "local_model_invoice_mismatch" in codes
    assert ledger2.row("B2#0").get("发票号") is None, "号码不一致时发票号必须留空进待补"


def test_recorded_local_model_response_is_marked_synthetic():
    with open(RECORDED, encoding="utf-8") as handle:
        doc = json.load(handle)
    assert doc["_provenance"] == "local synthetic replay, not a capture from the vendor API"
    assert "scripts/recorded_smoke.py --source live" in doc["_note"]
