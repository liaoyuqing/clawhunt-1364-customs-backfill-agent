"""模块三：开票公司与发票核对（差异清单精确到字段）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

from .models import Difference

CHECK_FIELDS = (
    ("buyer_name", "抬头公司名"),
    ("buyer_tax_no", "税号"),
    ("invoice_type", "发票类型"),
    ("product_name", "品名"),
    ("spec", "规格"),
    ("quantity", "数量"),
    ("amount", "金额"),
)


class CompanyNotFound(KeyError):
    """品类没有配置开票公司。"""


def company_for(category: str, cfg: Dict[str, Any]) -> Dict[str, str]:
    table = cfg.get("by_category", {}) or {}
    if category not in table:
        raise CompanyNotFound(category)
    return dict(table[category])


@dataclass
class InvoiceNotice:
    batch_no: str
    category: str
    company: str
    tax_no: str
    contact: str
    lines: List[Dict[str, str]] = field(default_factory=list)

    def as_text(self) -> str:
        head = "开票通知 %s -> %s（税号 %s，联系人 %s）" % (self.batch_no, self.company, self.tax_no, self.contact)
        body = ["%s %s 数量 %s 单价 %s" % (line["sku"], line["name"], line["qty"], line["price"]) for line in self.lines]
        return "\n".join([head] + body)


def build_notice(batch_no: str, category: str, cfg: Dict[str, Any], lines: List[Dict[str, str]]) -> InvoiceNotice:
    entry = company_for(category, cfg)
    return InvoiceNotice(
        batch_no=batch_no,
        category=category,
        company=str(entry["company"]),
        tax_no=str(entry["tax_no"]),
        contact=str(entry["contact"]),
        lines=list(lines),
    )


def _norm(value: Any) -> str:
    return str(value if value is not None else "").strip()


def compare(invoice: Dict[str, Any], contract: Dict[str, Any], batch_no: str = "") -> List[Difference]:
    """逐字段比，返回差异三元组；抬头公司与税号一定在比对范围内。"""
    out: List[Difference] = []
    for key, label in CHECK_FIELDS:
        expected = _norm(contract.get(key))
        actual = _norm(invoice.get(key))
        if key == "amount":
            try:
                same = Decimal(expected) == Decimal(actual)
            except Exception:
                same = expected == actual
            if not same:
                out.append(Difference(field=label, expected=expected, actual=actual, batch_no=batch_no))
            continue
        if expected != actual:
            out.append(Difference(field=label, expected=expected, actual=actual, batch_no=batch_no))
    return out


def compare_many(
    invoices: List[Dict[str, Any]],
    contract: Dict[str, Any],
    batch_no: str = "",
) -> List[Dict[str, Any]]:
    """多份发票一次比完，输出每份的差异清单（一致为空列表）。"""
    results: List[Dict[str, Any]] = []
    for invoice in invoices:
        diffs = compare(invoice, contract, batch_no=batch_no or _norm(invoice.get("batch_no")))
        results.append(
            {
                "invoice_no": _norm(invoice.get("invoice_no")),
                "differences": [d.as_dict() for d in diffs],
                "clean": not diffs,
            }
        )
    return results