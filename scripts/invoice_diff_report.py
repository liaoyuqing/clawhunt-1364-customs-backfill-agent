"""AC3 可复现证据：6 张合成发票与合同逐项核对，输出差异清单并自证无漏报、无多报。

用法：
    python scripts/invoice_diff_report.py

产出：
    reports/invoice_diff_report.md   人读版（结论 + 每张发票的差异明细）
    reports/invoice_diff_report.json 机读版（结论 + 明细 + 期望核对结果）

自证方式：脚本里写死一份独立于 core/ 的 EXPECTED 期望字典（每张发票期望哪几个字段进差异清单），
与 core.invoice.compare_many 的真实输出逐条比对。期望与实际只要有一处不同——漏报、多报、字段标签写错——
退出码就不是 0。所以这份报告本身就是「抽检无漏报」的机器证据，不依赖人的眼睛。
"""

from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.invoice import CHECK_FIELDS, compare_many  # noqa: E402

BATCH_NO = "B2026-0001"
CONTRACT_NO = "HT-2026-0001"

# 合同口径（模块一生成采购合同时同步给财务的开票基准），全部为虚构数据。
CONTRACT = {
    "buyer_name": "深圳前海跨境电子商务有限公司",
    "buyer_tax_no": "91440300MA5F0000XA",
    "invoice_type": "增值税专用发票",
    "product_name": "礼品卡套",
    "spec": "型号 M-1004",
    "quantity": "3",
    "amount": "9.60",
}

# 6 张发票：前 3 张与合同完全一致，后 3 张各含一处差异。
INVOICES = [
    dict(CONTRACT, invoice_no="INV-2026-1001"),
    dict(CONTRACT, invoice_no="INV-2026-1002"),
    dict(CONTRACT, invoice_no="INV-2026-1003"),
    dict(CONTRACT, invoice_no="INV-2026-1004", buyer_name="广州某某贸易有限公司"),
    dict(CONTRACT, invoice_no="INV-2026-1005", buyer_tax_no="91440300MA5F0000XB"),
    dict(CONTRACT, invoice_no="INV-2026-1006", spec="型号 M-9999"),
]

# 独立写死的期望：发票号 -> 期望进差异清单的字段标签（顺序即比对顺序）
EXPECTED = {
    "INV-2026-1001": [],
    "INV-2026-1002": [],
    "INV-2026-1003": [],
    "INV-2026-1004": ["抬头公司名"],
    "INV-2026-1005": ["税号"],
    "INV-2026-1006": ["规格"],
}

EXPECTED_DIRTY_SEQUENCE = ["抬头公司名", "税号", "规格"]


def build_report() -> dict:
    checked = compare_many(INVOICES, CONTRACT, batch_no=BATCH_NO)
    actual = {item["invoice_no"]: [diff["field"] for diff in item["differences"]] for item in checked}
    missed = []
    extra = []
    for invoice_no, want in EXPECTED.items():
        got = actual.get(invoice_no)
        if got is None:
            missed.append({"invoice_no": invoice_no, "missing_fields": list(want)})
            continue
        for field in want:
            if field not in got:
                missed.append({"invoice_no": invoice_no, "missing_field": field})
        for field in got:
            if field not in want:
                extra.append({"invoice_no": invoice_no, "extra_field": field})
        if [f for f in got if f in want] != list(want):
            missed.append({"invoice_no": invoice_no, "order_mismatch": {"expected": list(want), "actual": got}})
    dirty_sequence = [field for item in checked if not item["clean"] for field in (d["field"] for d in item["differences"])]
    order_ok = dirty_sequence == EXPECTED_DIRTY_SEQUENCE
    clean_count = sum(1 for item in checked if item["clean"])
    dirty_count = len(checked) - clean_count
    return {
        "batch_no": BATCH_NO,
        "contract_no": CONTRACT_NO,
        "check_fields": [label for _key, label in CHECK_FIELDS],
        "invoices_total": len(checked),
        "clean": clean_count,
        "dirty": dirty_count,
        "difference_count": len(dirty_sequence),
        "dirty_fields_sequence": dirty_sequence,
        "expected_dirty_fields_sequence": EXPECTED_DIRTY_SEQUENCE,
        "sequence_matches": order_ok,
        "missed": missed,
        "extra": extra,
        "no_missed": not missed,
        "no_extra": not extra,
        "verified": (not missed) and (not extra) and order_ok and clean_count == 3 and dirty_count == 3,
        "expected": EXPECTED,
        "results": [
            {
                "invoice_no": item["invoice_no"],
                "clean": item["clean"],
                "differences": item["differences"],
            }
            for item in checked
        ],
        "contract": CONTRACT,
    }


def render_markdown(report: dict) -> str:
    lines = [
        "# 发票与合同核对差异清单（AC3 实测输出）",
        "",
        "- 批次号：`%s`，合同号：`%s`" % (report["batch_no"], report["contract_no"]),
        "- 比对字段（%d 个）：%s" % (len(report["check_fields"]), "、".join(report["check_fields"])),
        "- 发票张数：%d（一致 %d 张，含差异 %d 张）" % (
            report["invoices_total"], report["clean"], report["dirty"]),
        "- 差异条目数：%d，差异字段序列：%s" % (
            report["difference_count"],
            "、".join(report["dirty_fields_sequence"]) if report["dirty_fields_sequence"] else "（无）",
        ),
        "- 期望字段序列：%s，序列一致：%s" % (
            "、".join(report["expected_dirty_fields_sequence"]), "是" if report["sequence_matches"] else "否"),
        "- 漏报条目：%d，多报条目：%d，结论：%s" % (
            len(report["missed"]), len(report["extra"]), "无漏报、无多报" if report["verified"] else "不通过"),
        "",
        "## 合同基准（开票口径）",
        "",
        "| 字段 | 合同值 |",
        "| --- | --- |",
    ]
    for key, value in report["contract"].items():
        lines.append("| %s | %s |" % (key, value))
    lines.extend(["", "## 逐张发票核对结果", "", "| 发票号 | 结论 | 差异字段 | 合同值 → 发票值 |", "| --- | --- | --- | --- |"])
    for item in report["results"]:
        if item["clean"]:
            lines.append("| %s | 一致 | — | — |" % item["invoice_no"])
            continue
        for diff in item["differences"]:
            lines.append(
                "| %s | 有差异 | %s | %s → %s |"
                % (item["invoice_no"], diff["field"], diff["expected"], diff["actual"]),
            )
    lines.extend(["", "## 期望核对（脚本内独立写死的期望字典）", "", "| 发票号 | 期望差异字段 | 实际差异字段 | 结论 |", "| --- | --- | --- | --- |"])
    actual = {item["invoice_no"]: [d["field"] for d in item["differences"]] for item in report["results"]}
    for invoice_no, want in report["expected"].items():
        got = actual.get(invoice_no, [])
        ok = got == list(want)
        lines.append(
            "| %s | %s | %s | %s |"
            % (invoice_no, "、".join(want) if want else "（无）", "、".join(got) if got else "（无）", "一致" if ok else "不一致"),
        )
    lines.extend(["", "复现命令：`python scripts/invoice_diff_report.py`（退出码 0 表示期望核对全过）。", ""])
    return "\n".join(lines)


def main() -> int:
    report = build_report()
    out_dir = os.path.join(ROOT, "reports")
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, "invoice_diff_report.json")
    md_path = os.path.join(out_dir, "invoice_diff_report.md")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write(render_markdown(report))
    print(
        "invoices %d clean %d dirty %d diffs %d fields %s missed %d extra %d verified %s"
        % (
            report["invoices_total"], report["clean"], report["dirty"], report["difference_count"],
            ",".join(report["dirty_fields_sequence"]) or "-", len(report["missed"]), len(report["extra"]),
            "yes" if report["verified"] else "no",
        ),
    )
    print("report -> %s" % json_path)
    print("report -> %s" % md_path)
    return 0 if report["verified"] else 1


if __name__ == "__main__":
    sys.exit(main())
