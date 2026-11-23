"""模块四：结余 Decimal 计算、超用拒绝、四类原件归档与共享链接。"""

from __future__ import annotations

import os

import pytest

from core.archive import (
    ARCHIVE_KINDS,
    ArchiveIncomplete,
    ArchiveStore,
    InvoiceUsage,
    OverUsed,
    consistency_issues,
    remaining,
)


def test_module4_remaining_formula():
    assert remaining("1000.00", []) == "1000.00"
    assert remaining("1000.00", ["100", "50.50"]) == "849.50"
    assert remaining("0.30", ["0.10", "0.20"]) == "0.00"


def test_module4_usage_accrues_and_blocks_over_use():
    usage = InvoiceUsage(invoice_no="INV-1", invoiced_total="100.00")
    assert usage.use("B1", "40") == "60.00"
    assert usage.use("B2", "60") == "0.00"
    with pytest.raises(OverUsed):
        usage.use("B3", "1")
    with pytest.raises(OverUsed):
        usage.use("B4", "0")
    assert len(usage.used) == 2


def test_module4_recalc_stamp_changes_with_usage():
    usage = InvoiceUsage(invoice_no="INV-2", invoiced_total="50.00")
    first = usage.recalc_stamp("2026-11-23T10:00:00+08:00")
    usage.use("B9", "10")
    second = usage.recalc_stamp("2026-11-23T10:00:00+08:00")
    assert first != second
    assert len(second) == 12


def test_module4_consistency_lists_all_six_fields():
    invoice = {
        "invoice_type": "专用",
        "invoice_no": "INV-9",
        "product_name": "桌面收纳盒",
        "quantity": "0",
        "spec": "M-9999",
        "unit": "箱",
    }
    batch = {
        "batch_no": "B2026-0009",
        "invoice_type": "专用",
        "invoice_no": "INV-9",
        "product_name": "桌面收纳盒",
        "quantity": "120",
        "spec": "M-1001",
        "unit": "个",
    }
    issues = consistency_issues(invoice, batch)
    assert [issue.column for issue in issues] == ["数量", "规格", "计量单位"]
    assert all(issue.code == "invoice_inconsistent" for issue in issues)


def test_module4_archive_needs_all_four_kinds(tmp_path):
    store = ArchiveStore(str(tmp_path))
    store.put("B2026-0001", "合同", "contract.pdf", b"c")
    store.put("B2026-0001", "送货单", "delivery.pdf", b"d")
    assert store.missing("B2026-0001") == ["发票原件", "对公支付凭证"]
    with pytest.raises(ArchiveIncomplete):
        store.link("B2026-0001")
    store.put("B2026-0001", "发票原件", "invoice.pdf", b"i")
    store.put("B2026-0001", "对公支付凭证", "payment.pdf", b"p")
    assert store.missing("B2026-0001") == []
    link = store.link("B2026-0001")
    assert link.startswith("https://")
    assert link.endswith("B2026-0001")
    expected_files = {
        "合同": "contract.pdf",
        "送货单": "delivery.pdf",
        "发票原件": "invoice.pdf",
        "对公支付凭证": "payment.pdf",
    }
    for kind in ARCHIVE_KINDS:
        target = os.path.join(str(tmp_path), "B2026-0001", expected_files[kind])
        assert os.path.exists(target), kind
    with pytest.raises(ValueError):
        store.put("B2026-0002", "未知类型", "x.pdf", b"")