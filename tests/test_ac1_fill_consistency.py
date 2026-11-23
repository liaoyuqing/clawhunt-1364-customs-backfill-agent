"""AC1：50 批次逐字段抽检，一致率 ≥99.5%，来源元数据零缺失，缺失项进待补清单。"""

from __future__ import annotations

import sys

from core.checks import pending_list
from core.models import SOURCE_KINDS

sys.path.insert(0, "scripts")

from sample50 import compare  # noqa: E402


def test_ac1_sample50_consistency_at_least_995(filled, expected_bundle):
    ledger, _results = filled
    consistent, undecidable, diffs = compare(ledger, expected_bundle)
    decidable = consistent + len(diffs)
    assert decidable >= 1500
    assert diffs == []
    assert consistent / decidable >= 0.995
    assert undecidable >= len(expected_bundle["source_errors"])


def test_ac1_every_cell_has_source_kind(filled):
    ledger, _results = filled
    assert ledger.cells_missing_source() == []
    counts = {kind: 0 for kind in SOURCE_KINDS}
    for row in ledger.rows():
        for cell in row.cells.values():
            assert cell.source.kind in SOURCE_KINDS
            assert cell.source.ref
            assert cell.source.at
            counts[cell.source.kind] += 1
    assert counts["api"] > 0
    assert counts["config"] > 0
    assert counts["api"] + counts["config"] == sum(counts.values())


def test_ac1_missing_fields_become_pending_list(filled):
    ledger, results = filled
    issues = [issue for result in results for issue in result.issues]
    codes = {issue.code for issue in issues}
    assert "missing_field" in codes
    pending = pending_list(issues)
    assert pending
    assert pending == sorted(pending, key=lambda i: (i["batch_no"], i["column"], i["code"]))
    for item in pending:
        assert set(item) == {"code", "batch_no", "column", "message"}


def test_ac1_wrong_hscode_lowers_rate_and_is_located(filled, expected_bundle, rules, batches, products, suppliers):
    """反例：把某产品的 HSCODE 映射写错，一致率必须下降且差异精确指到批次与列。"""
    from core.declare import BatchContext, BatchFiller
    from core.ledger import Ledger

    broken_products = {sku: dict(item) for sku, item in products.items()}
    broken_products["SKU-1001"]["hscode"] = "3924900001"
    ledger = Ledger(rules.columns)
    filler = BatchFiller(rules)
    for batch in batches:
        filler.fill(ledger, BatchContext(batch=batch, products=broken_products, suppliers=suppliers))
    consistent, undecidable, diffs = compare(ledger, expected_bundle)
    decidable = consistent + len(diffs)
    assert diffs, "写错 HSCODE 后必须出现差异"
    assert consistent / decidable < 0.995
    hit = [d for d in diffs if d["column"] == "HSCODE"]
    assert hit
    assert all(d["actual"] == "3924900001" for d in hit)
    assert all(d["row_key"].startswith("B2026-") for d in hit)