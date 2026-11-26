"""50 批次抽检：逐格比对回填结果与独立参考实现，输出一致率与三类差异。

分母是台账全部 23 列（模块二 16 列 + 模块四 7 列），不再只看模块二的 16 列。

--source 决定数据从哪来：
    fixture    仓库自带合成夹具（报告里 origin=fixture）
    recorded   fixtures/recorded/*.json 本地合成录制响应（origin=recorded），默认
    capture    scripts/live_capture.py 落盘的真实响应回放（origin=live，配 --capture-dir）
    lingxing   现场直连真实领星开放平台（origin=live，凭据只从环境变量读）

关于 AC#1 的口径（买方第 2 次驳回的正题）：
    合成源（fixture / recorded）跑出的 1.000000 只能证明「回填引擎与独立参考实现一致」，
    **不能**当成「与客户源系统字段级一致率达标」的证据。本脚本据此在报告里写两个字段：
      counts_as_ac1_evidence  —— 只有 origin=live 才是 true；
      ac1_evidence_status     —— 合成源固定为 unverified_on_live_account 并附原因与复现步骤。
    判定行（末行 stdout）仍然只谈引擎自洽性，不冒充验收结论。

源系统自身的错误（缺 HSCODE 等）单独列在 source_errors，不计入分母。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import pipeline  # noqa: E402
from core.config import Rules  # noqa: E402
from core.models import SOURCE_KINDS  # noqa: E402
from core.module4 import InvoiceLedger  # noqa: E402
from source_data import SourceDataError, load_source_data  # noqa: E402

FIX = os.path.join(ROOT, "fixtures")
EXPECTED = os.path.join(FIX, "expected.json")

# 合成源跑出的一致率不能作为 AC#1 的达标证据，报告里如实写清楚原因。
AC1_NOT_VERIFIED_REASON = (
    "未在真实账号上取得（原因：领星开放接口需付费套餐才开通，且 openapi.lingxing.com 从交付方网络 "
    "TCP 443 不可达）；本文件的一致率来自合成源，只证明回填引擎与独立参考实现自洽"
)
AC1_REPRODUCE_STEPS = [
    "1) 在客户环境配好 LINGXING_BASE_URL / LINGXING_APP_ID / LINGXING_APP_SECRET（领星开放后台 API 信息页取 AppID/AppSecret，并配 IP 白名单）",
    "2) python scripts/live_capture.py --source lingxing --out captures/live  # 落盘真实响应 + manifest.json（origin=live）",
    "3) python scripts/sample50.py --source capture --capture-dir captures/live  # 用真实响应重算一致率，报告里 source_origin=live",
    "4) 报告 ac1_evidence_status 变成 verified_on_live_account，一致率才是 AC#1 的达标证据",
]


def load(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as handle:
        return json.load(handle)


def build_ledger(rules, source="recorded", capture_dir=None):
    """与 core.cli 同一条流水线：模块二回填 + 模块四发票/归档回填。"""
    data = load_source_data(source, capture_dir=capture_dir)
    invoices = InvoiceLedger(records=data["invoices"])
    result = pipeline.run_pipeline(
        rules,
        data["batches"],
        data["products"],
        data["suppliers"],
        invoices,
    )
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
    return result["ledger"], result["fills"], data


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


def ac1_evidence(origin, rate, diffs, source):
    """AC#1 的证据状态：只有 live 才宣告「已验证」。"""
    if origin == "live":
        verified = bool(rate >= 0.995 and not diffs)
        return {
            "counts_as_ac1_evidence": True,
            "ac1_evidence_status": "verified_on_live_account" if verified else "live_source_below_threshold",
            "reason": "" if verified else "真实源上一致率 %.6f 未达 0.995 或存在差异" % rate,
            "source": source,
            "reproduce_steps": [],
        }
    return {
        "counts_as_ac1_evidence": False,
        "ac1_evidence_status": "unverified_on_live_account",
        "reason": AC1_NOT_VERIFIED_REASON,
        "source": source,
        "reproduce_steps": AC1_REPRODUCE_STEPS,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python scripts/sample50.py")
    parser.add_argument(
        "--source",
        default="recorded",
        choices=["fixture", "recorded", "capture", "lingxing"],
        help="数据来源，默认 recorded（本地合成录制回放）",
    )
    parser.add_argument(
        "--capture-dir",
        default="",
        help="真实抓取目录（scripts/live_capture.py 的输出），配合 --source capture",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    rules = Rules()
    try:
        ledger, results, data = build_ledger(rules, args.source, args.capture_dir or None)
    except SourceDataError as exc:
        print("数据源不可用：%s" % exc, file=sys.stderr)
        return 2
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
    provenance = data["provenance"]
    origin = str(provenance.get("origin", ""))
    source_errors = list(expected_bundle.get("source_errors", []))
    payload = {
        "batches": len(data["batches"]),
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
        "source": provenance.get("source", args.source),
        "source_origin": origin,
        "source_origin_detail": provenance.get("origin_detail", ""),
        "source_host": provenance.get("host", ""),
        "source_files": provenance.get("files", []),
        "source_fetched_at": provenance.get("fetched_at", ""),
        "source_label": provenance.get("label", ""),
        "source_synthetic": bool(provenance.get("synthetic", False)),
        "source_mapping_notes": provenance.get("mapping_notes", []),
        "source_errors": source_errors,
        "source_error_count": len(source_errors),
    }
    payload.update(ac1_evidence(origin, rate, diffs, payload["source"]))
    out_dir = os.path.join(ROOT, "reports")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "sample50_report.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    print(
        "batches %d rows %d columns %d compared %d consistent %d undecidable %d rate %.6f source_missing %d diffs %d"
        % (payload["batches"], payload["rows"], payload["columns"], decidable, consistent, undecidable, rate, len(source_missing), len(diffs)),
    )
    print("source %s origin %s host %s fetched_at %s" % (payload["source"], origin, payload["source_host"], payload["source_fetched_at"]))
    print("source_errors %d (源系统自身错误，不计入分母)" % payload["source_error_count"])
    print(
        "ac1_evidence %s counts_as_ac1_evidence=%s"
        % (payload["ac1_evidence_status"], str(payload["counts_as_ac1_evidence"]).lower()),
    )
    if not payload["counts_as_ac1_evidence"]:
        print("说明：%s" % payload["reason"])
    print("report -> %s" % path)
    return 0 if (rate >= 0.995 and not source_missing and not diffs) else 1


if __name__ == "__main__":
    sys.exit(main())
