"""必填、格式与勾稽校验：三类问题一次性全列，不报一条就停。"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional

from .models import Issue, Row


def missing_issues(row: Row, required: Iterable[str]) -> List[Issue]:
    out: List[Issue] = []
    for column in required:
        cell = row.get(column)
        if cell is None or not cell.value.strip():
            out.append(
                Issue(
                    code="missing_field",
                    batch_no=row.batch_no,
                    column=column,
                    message="必填字段为空",
                )
            )
    return out


def hscode_issues(row: Row, pattern: str) -> List[Issue]:
    cell = row.get("HSCODE")
    if cell is None or not cell.value.strip():
        return []
    if not re.fullmatch(pattern, cell.value.strip()):
        return [
            Issue(
                code="bad_hscode",
                batch_no=row.batch_no,
                column="HSCODE",
                message="海关编码应为 10 位数字，实际 %r" % cell.value,
            )
        ]
    return []


def price_issues(row: Row) -> List[Issue]:
    cell = row.get("报关单价")
    if cell is None or not cell.value.strip():
        return []
    try:
        value = float(cell.value)
    except ValueError:
        return [
            Issue(
                code="bad_price",
                batch_no=row.batch_no,
                column="报关单价",
                message="申报单价不是数字：%r" % cell.value,
            )
        ]
    if value <= 0:
        return [
            Issue(
                code="bad_price",
                batch_no=row.batch_no,
                column="报关单价",
                message="申报单价必须大于 0，实际 %r" % cell.value,
            )
        ]
    return []


def reconciliation_issues(
    row: Row,
    rules: List[Dict],
    left_values: Optional[Dict[str, str]] = None,
    right_values: Optional[Dict[str, str]] = None,
) -> List[Issue]:
    """勾稽：左右两值不相等即报一条，缺值不报（缺值由 missing 负责）。"""
    left_values = left_values or {}
    right_values = right_values or {}
    out: List[Issue] = []
    for rule in rules:
        left = left_values.get(str(rule["id"]))
        right = right_values.get(str(rule["id"]))
        if left is None or right is None:
            continue
        if str(left) != str(right):
            out.append(
                Issue(
                    code="reconciliation_%s" % rule["id"],
                    batch_no=row.batch_no,
                    column=str(rule.get("name", rule["id"])),
                    message="勾稽不符：%s" % rule.get("name", rule["id"]),
                )
            )
    return out


def all_issues(
    row: Row,
    required: Iterable[str],
    hscode_pattern: str,
    reconciliation: Optional[List[Dict]] = None,
    left_values: Optional[Dict[str, str]] = None,
    right_values: Optional[Dict[str, str]] = None,
) -> List[Issue]:
    out: List[Issue] = []
    out.extend(missing_issues(row, required))
    out.extend(hscode_issues(row, hscode_pattern))
    out.extend(price_issues(row))
    out.extend(reconciliation_issues(row, reconciliation or [], left_values, right_values))
    return out


def pending_list(issues: List[Issue]) -> List[Dict[str, str]]:
    """待补清单：按批次号与列名排序，便于业务逐条补。"""
    ordered = sorted(issues, key=lambda i: (i.batch_no, i.column, i.code))
    return [issue.as_dict() for issue in ordered]