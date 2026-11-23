"""AC3：发票与合同逐项核对输出差异清单，抬头公司与税号必须在比对范围内。"""

from __future__ import annotations

import pytest

from core.invoice import build_notice, compare, compare_many, company_for

CONTRACT = {
    "buyer_name": "深圳市某某电子商务有限公司",
    "buyer_tax_no": "91440300MA5F9999XZ",
    "invoice_type": "增值税专用发票",
    "product_name": "桌面收纳盒",
    "spec": "型号 M-1001",
    "quantity": "120",
    "amount": "1536.00",
}


def _invoice(**overrides):
    payload = dict(CONTRACT)
    payload.update(overrides)
    payload.setdefault("invoice_no", "INV-0001")
    return payload


def test_ac3_clean_invoice_produces_empty_diff():
    diffs = compare(_invoice(), CONTRACT, batch_no="B2026-0001")
    assert diffs == []


@pytest.mark.parametrize(
    "overrides,label",
    [
        ({"buyer_name": "广州某某贸易有限公司"}, "抬头公司名"),
        ({"buyer_tax_no": "91440300MA5F9999XY"}, "税号"),
        ({"spec": "型号 M-1002"}, "规格"),
        ({"quantity": "120.0"}, "数量"),
        ({"amount": "1536.01"}, "金额"),
        ({"product_name": "桌面笔筒"}, "品名"),
        ({"invoice_type": "增值税普通发票"}, "发票类型"),
    ],
)
def test_ac3_single_field_mismatch_is_reported(overrides, label):
    diffs = compare(_invoice(**overrides), CONTRACT, batch_no="B2026-0002")
    assert [d.field for d in diffs] == [label]
    assert diffs[0].batch_no == "B2026-0002"
    assert diffs[0].as_dict()["field"] == label


def test_ac3_six_invoices_three_clean_three_dirty():
    invoices = [
        _invoice(invoice_no="INV-1001"),
        _invoice(invoice_no="INV-1002"),
        _invoice(invoice_no="INV-1003"),
        _invoice(invoice_no="INV-1004", buyer_name="错误抬头有限公司"),
        _invoice(invoice_no="INV-1005", buyer_tax_no="91440300MA5F0000XX"),
        _invoice(invoice_no="INV-1006", spec="型号 M-9999"),
    ]
    results = compare_many(invoices, CONTRACT, batch_no="B2026-0003")
    assert [r["clean"] for r in results] == [True, True, True, False, False, False]
    assert [r["invoice_no"] for r in results if not r["clean"]] == ["INV-1004", "INV-1005", "INV-1006"]
    fields = [item["field"] for r in results for item in r["differences"]]
    assert fields == ["抬头公司名", "税号", "规格"]


def test_ac3_amount_compared_numerically_not_textually():
    diffs = compare(_invoice(amount="1536.0"), CONTRACT)
    assert diffs == []


def test_ac3_notice_matches_configured_company(rules):
    cfg = rules.invoice_companies
    entry = company_for("家居", cfg)
    notice = build_notice("B2026-0004", "家居", cfg, [{"sku": "SKU-1001", "name": "桌面收纳盒", "qty": "120", "price": "12.80"}])
    assert notice.company == entry["company"]
    assert notice.tax_no == entry["tax_no"]
    assert notice.contact == entry["contact"]
    text = notice.as_text()
    assert entry["company"] in text and entry["tax_no"] in text and entry["contact"] in text
    with pytest.raises(KeyError):
        company_for("不存在的品类", cfg)