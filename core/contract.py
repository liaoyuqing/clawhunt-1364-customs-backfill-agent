"""模块一：采购合同生成与要件检查。合同品名=开票名=报关名，三处强制一致。"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

from .models import Issue


class NameMismatch(ValueError):
    """合同品名与报关名/开票名不一致。"""


@dataclass(frozen=True)
class ContractLine:
    sku: str
    product_name: str
    invoice_name: str
    declare_name: str
    spec: str
    unit: str
    qty: str
    unit_price: str

    def as_dict(self) -> Dict[str, str]:
        return {
            "sku": self.sku,
            "product_name": self.product_name,
            "invoice_name": self.invoice_name,
            "declare_name": self.declare_name,
            "spec": self.spec,
            "unit": self.unit,
            "qty": self.qty,
            "unit_price": self.unit_price,
        }


def assert_name_consistency(lines: List[ContractLine]) -> None:
    """三个名字必须字面相同，不允许「差不多」。"""
    for line in lines:
        if not (line.product_name == line.invoice_name == line.declare_name):
            raise NameMismatch(
                "sku %s: product=%r invoice=%r declare=%r"
                % (line.sku, line.product_name, line.invoice_name, line.declare_name),
            )


@dataclass
class Contract:
    contract_no: str
    supplier: str
    buyer: str
    lines: List[ContractLine] = field(default_factory=list)
    stamp_received: bool = False

    def total_amount(self) -> str:
        total = Decimal("0")
        for line in self.lines:
            total += Decimal(line.qty) * Decimal(line.unit_price)
        return format(total.quantize(Decimal("0.01")), "f")

    def as_text(self) -> str:
        head = ["采购合同 %s" % self.contract_no, "供方：%s" % self.supplier, "需方：%s" % self.buyer, ""]
        rows = [
            "%s | %s | 规格 %s | %s %s | 单价 %s"
            % (line.sku, line.product_name, line.spec, line.qty, line.unit, line.unit_price)
            for line in self.lines
        ]
        return "\n".join(head + rows + ["", "合计 %s" % self.total_amount()])


def build_contract(contract_no: str, supplier: str, buyer: str, lines: List[ContractLine]) -> Contract:
    assert_name_consistency(lines)
    return Contract(contract_no=contract_no, supplier=supplier, buyer=buyer, lines=list(lines))


REQUIRED_ATTACHMENTS = ("供应商营业执照", "报价单", "盖章回传件")


def requirement_issues(contract: Contract, attachments: Optional[List[str]] = None) -> List[Issue]:
    """要件检查：缺什么一次性全列，不短路。"""
    attachments = list(attachments or [])
    out: List[Issue] = []
    for name in REQUIRED_ATTACHMENTS:
        if name not in attachments:
            out.append(
                Issue(
                    code="missing_requirement",
                    batch_no=contract.contract_no,
                    column=name,
                    message="合同要件缺失：%s" % name,
                )
            )
    if not contract.lines:
        out.append(
            Issue(
                code="empty_contract",
                batch_no=contract.contract_no,
                column="lines",
                message="合同没有任何明细行",
            )
        )
    try:
        assert_name_consistency(contract.lines)
    except NameMismatch as exc:
        out.append(
            Issue(
                code="name_mismatch",
                batch_no=contract.contract_no,
                column="品名",
                message=str(exc),
            )
        )
    if not contract.stamp_received:
        out.append(
            Issue(
                code="stamp_missing",
                batch_no=contract.contract_no,
                column="盖章回传件",
                message="供应商盖章回传件未归档",
            )
        )
    return out