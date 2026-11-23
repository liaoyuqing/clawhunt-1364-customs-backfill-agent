"""AC3：7 个比对字段逐一单字段扰动都恰好报 1 条，3 张脏发票的差异字段序列不多不少。"""

from __future__ import annotations

import pytest

from core.invoice import CHECK_FIELDS, compare, compare_many

CONTRACT = {
    "buyer_name": "深圳前海跨境电子商务有限公司",
    "buyer_tax_no": "91440300MA5F0000XA",
    "invoice_type": "增值税专用发票",
    "product_name": "礼品卡套",
    "spec": "型号 M-1004",
    "quantity": "3",
    "amount": "9.60",
}

# 每个字段一个「只差这一处」的扰动值；其余六个字段保持合同值不动。
PERTURB = {
    "buyer_name": "广州某某贸易有限公司",
    "buyer_tax_no": "91440300MA5F0000XB",
    "invoice_type": "增值税普通发票",
    "product_name": "桌面收纳盒",
    "spec": "型号 M-9999",
    "quantity": "4",
    "amount": "9.61",
}


def test_perturb_table_covers_every_checked_field():
    """扰动表必须与 core.invoice.CHECK_FIELDS 一一对应，字段加了没补表就直接红。"""
    assert [key for key, _label in CHECK_FIELDS] == list(PERTURB.keys())
    assert len(CHECK_FIELDS) == 7
    for key, value in PERTURB.items():
        assert value != CONTRACT[key]


@pytest.mark.parametrize("key,label", list(CHECK_FIELDS))
def test_single_field_perturbation_yields_exactly_one_diff(key, label):
    invoice = dict(CONTRACT, invoice_no="INV-P-%s" % key)
    invoice[key] = PERTURB[key]
    diffs = compare(invoice, CONTRACT, batch_no="B2026-0001")
    assert len(diffs) == 1, [(d.field, d.expected, d.actual) for d in diffs]
    assert diffs[0].field == label
    assert diffs[0].actual == PERTURB[key]
    assert diffs[0].expected == CONTRACT[key]
    assert diffs[0].batch_no == "B2026-0001"


def test_unperturbed_invoice_is_clean():
    assert compare(dict(CONTRACT, invoice_no="INV-CLEAN"), CONTRACT, batch_no="B2026-0001") == []


def test_three_dirty_invoices_produce_exact_field_sequence():
    """3 净 3 脏：脏的三张各一处差异，合起来恰好是 抬头公司名/税号/规格 三条，无多报。"""
    invoices = [
        dict(CONTRACT, invoice_no="INV-2026-1001"),
        dict(CONTRACT, invoice_no="INV-2026-1002"),
        dict(CONTRACT, invoice_no="INV-2026-1003"),
        dict(CONTRACT, invoice_no="INV-2026-1004", buyer_name=PERTURB["buyer_name"]),
        dict(CONTRACT, invoice_no="INV-2026-1005", buyer_tax_no=PERTURB["buyer_tax_no"]),
        dict(CONTRACT, invoice_no="INV-2026-1006", spec=PERTURB["spec"]),
    ]
    results = compare_many(invoices, CONTRACT, batch_no="B2026-0001")
    assert [r["clean"] for r in results] == [True, True, True, False, False, False]
    assert [len(r["differences"]) for r in results] == [0, 0, 0, 1, 1, 1]
    fields = [diff["field"] for r in results for diff in r["differences"]]
    assert fields == ["抬头公司名", "税号", "规格"]


def test_report_script_output_matches_independent_expected():
    """scripts/invoice_diff_report.py 里独立写死的期望与实际输出必须一致（跑脚本本身）。"""
    import importlib.util
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "scripts", "invoice_diff_report.py")
    spec = importlib.util.spec_from_file_location("invoice_diff_report", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = module.build_report()
    assert report["verified"] is True
    assert report["no_missed"] is True and report["no_extra"] is True
    assert report["clean"] == 3 and report["dirty"] == 3
    assert report["dirty_fields_sequence"] == report["expected_dirty_fields_sequence"] == ["抬头公司名", "税号", "规格"]
    actual = {item["invoice_no"]: [d["field"] for d in item["differences"]] for item in report["results"]}
    assert actual == module.EXPECTED
