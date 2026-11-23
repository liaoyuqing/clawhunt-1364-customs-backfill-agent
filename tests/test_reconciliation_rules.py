"""勾稽规则 R1/R2/R3：三条都必须能真的报出问题，不能恒真。"""

from __future__ import annotations

from core import pipeline
from core.config import Rules
from core.declare import contract_no_issues, merge_batches


def _run_reconciliation():
    rules = Rules()
    batches, products, suppliers = pipeline.load_fixtures("fixtures/reconciliation")
    invoices = pipeline.load_invoices("fixtures/invoices/ledger.json")
    return pipeline.run_pipeline(rules, batches, products, suppliers, invoices)


def test_reconciliation_fixture_raises_all_three_codes():
    """反例夹具上必须同时看到 R1、R2、R3 三条勾稽问题。"""
    result = _run_reconciliation()
    codes = sorted({issue.code for issue in result["issues"]})
    assert "reconciliation_R1" in codes
    assert "reconciliation_R2" in codes
    assert "reconciliation_R3" in codes


def test_r1_compares_purchase_qty_with_shipment_qty():
    """R1：采购数量 10 vs 装箱数量 8，必须报一条。"""
    result = _run_reconciliation()
    r1 = [issue for issue in result["issues"] if issue.code == "reconciliation_R1"]
    assert len(r1) == 1
    assert r1[0].batch_no == "R2026-0001"
    assert r1[0].column == "采购数量与装箱数量一致"


def test_r2_compares_declared_unit_with_product_unit():
    """R2：报关单位写成了「托盘」，产品基础单位是「个」，必须报一条。"""
    result = _run_reconciliation()
    r2 = [issue for issue in result["issues"] if issue.code == "reconciliation_R2"]
    assert len(r2) == 1
    assert r2[0].batch_no == "R2026-0002"
    assert "报关单位" in r2[0].column


def test_r3_rejects_contract_no_shared_by_two_batches():
    """R3：两个批次共用同一合同号，必须报出来（同一批次内重复同样报）。"""
    result = _run_reconciliation()
    r3 = [issue for issue in result["issues"] if issue.code == "reconciliation_R3"]
    assert r3
    assert all("RC-HT-0001" in issue.message for issue in r3)
    assert "R2026-0002" in {issue.batch_no for issue in r3}


def test_r3_dedupes_within_one_batch_and_flags_across_batches(rules, products, suppliers):
    """同一批次多行共用一个合同号不算冲突；跨批次才算。"""
    from core.declare import BatchContext, BatchFiller
    from core.ledger import Ledger

    def make(batch_no, contract_no, lines):
        return {"batch_no": batch_no, "shipment_no": "S-" + batch_no, "contract_no": contract_no, "lines": lines}

    line = {"sku": "SKU-1001", "qty": 1, "cartons": 1, "weight_kg": "0.1", "supplier_id": "S01",
            "purchase_qty": 1, "shipment_qty": 1}
    ledger = Ledger(rules.columns)
    filler = BatchFiller(rules)
    for batch in (make("R3-A", "C-SAME", [dict(line), dict(line)]), make("R3-B", "C-SAME", [dict(line)])):
        filler.fill(ledger, BatchContext(batch=batch, products=products, suppliers=suppliers))
    issues = contract_no_issues([ledger])
    assert len(issues) == 1, "只有跨批次的那一行算冲突"
    assert issues[0].batch_no == "R3-B"


def test_reconciliation_rules_declare_left_and_right_sources(rules):
    """R2 的 left/right 必须是不同的来源（declare_unit vs product_unit），不能再自比。"""
    table = {str(item["id"]): item for item in rules.reconciliation}
    assert table["R2"]["left"] == "declare_unit"
    assert table["R2"]["right"] == "product_unit"
    assert table["R2"]["left"] != table["R2"]["right"]
    assert "R3" not in table, "R3 由 contract_no_issues 做批次间校验，不在自比表里"


def test_r2_passes_on_clean_50_batches(rules, batches, products, suppliers):
    """干净的 50 批次上 R1/R2 不产生误报。"""
    result = pipeline.run_pipeline(
        rules, batches, products, suppliers,
        pipeline.load_invoices(__import__("os").path.join("fixtures", "invoices", "ledger.json")),
    )
    codes = [issue.code for issue in result["issues"]]
    assert "reconciliation_R1" not in codes
    assert "reconciliation_R2" not in codes
    assert "reconciliation_R3" not in codes