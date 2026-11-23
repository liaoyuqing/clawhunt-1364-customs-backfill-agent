"""50 批次抽检：逐格比对回填结果与独立参考实现，输出一致率与三类差异。

分母是台账全部 23 列（模块二 16 列 + 模块四 7 列），不再只看模块二的 16 列。
"""

from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import pipeline  # noqa: E402
from core.config import Rules  # noqa: E402
from core.models import SOURCE_KINDS  # noqa: E402

FIX = os.path.join(ROOT, "fixtures")


def load(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as handle:
        return json.load(handle)


def build_ledger(rules):
    """与 core.cli 同一条流水线：模块二回填 + 模块四发票/归档回填。"""
    batches, products, suppliers = pipeline.load_fixtures(os.path.join(FIX, "lingxing"))
    invoices = pipeline.load_invoices(os.path.join(FIX, "invoices", "ledger.json"))
    result = pipeline.run_pipeline(rules, batches, products, suppliers, invoices)
    """单位换算留痕单独落一份，台账列序不变但换算前后可查。"""
    out_dir = os.path.join(ROOT, "reports")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "unit_conversions.json"), "w", encoding="utf-8") as handle:
        json.dump(
            {"total": len(result["conversions"]), "rows": result["conversions"]},
            handle,
            ensure_ascii=False,
            indent=2,
        )
    return result["ledger"], result["fills"], batches


def compare(ledger, expected_bundle):
    """返回一致 / 不一致 / 不可判定 三类计数与差异明细。"""
    expected = expected_bundle["expected"]
    consistent = 0
    undecidable = 0
    diffs = []
    for row_key, cells in expected.items():
        if row_key not in ledger.keys():
            diffs.append({"row_key": row_key, "column": "*", "expected": "row exists", "actual": "row missing", "reason": "mapping_error"})
            continue
        row = ledger.row(row_key)
        for column, spec in cells.items():
            if not spec["decidable"]:
                undecidable += 1
                continue
            actual = row.value(column)
            if actual == spec["expected"]:
                consistent += 1
                continue
            cell = row.get(column)
            kind = cell.source.kind if cell is not None else "missing"
            reason = "vision_error" if kind == "vision" else "mapping_error"
            diffs.append(
                {
                    "row_key": row_key,
                    "column": column,
                    "expected": spec["expected"],
                    "actual": actual,
                    "reason": reason,
                }
            )
    return consistent, undecidable, diffs


def main() -> int:
    rules = Rules()
    ledger, results, batches = build_ledger(rules)
    expected_bundle = load("expected.json")
    consistent, undecidable, diffs = compare(ledger, expected_bundle)
    decidable = consistent + len(diffs)
    rate = (consistent / decidable) if decidable else 0.0
    source_missing = ledger.cells_missing_source()
    kinds = {}
    for row in ledger.rows():
        for cell in row.cells.values():
            if cell.source.kind not in SOURCE_KINDS:
                kinds[cell.source.kind] = kinds.get(cell.source.kind, 0) + 1
    issues = [issue.as_dict() for result in results for issue in result.issues]
    payload = {
        "batches": len(batches),
        "rows": len(ledger.keys()),
        "columns": len(rules.columns),
        "cells_compared": decidable,
        "cells_consistent": consistent,
        "cells_undecidable": undecidable,
        "consistency_rate": round(rate, 6),
        "passes_threshold": rate >= 0.995,
        "source_kind_missing": len(source_missing),
        "bad_source_kinds": kinds,
        "differences": diffs,
        "difference_count": len(diffs),
        "issues": issues,
        "issue_count": len(issues),
    }
    out_dir = os.path.join(ROOT, "reports")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "sample50_report.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    print(
        "batches %d rows %d columns %d compared %d consistent %d undecidable %d rate %.6f source_missing %d diffs %d"
        % (payload["batches"], payload["rows"], payload["columns"], decidable, consistent, undecidable, rate, len(source_missing), len(diffs)),
    )
    print("report -> %s" % path)
    return 0 if (rate >= 0.995 and not source_missing and not diffs) else 1


if __name__ == "__main__":
    sys.exit(main())