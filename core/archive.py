"""模块四：发票回填、结余实时计算、四类原件归档。金额与数量一律 Decimal。"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

from .models import Difference, Issue

ARCHIVE_KINDS = ("合同", "送货单", "发票原件", "对公支付凭证")


class ArchiveIncomplete(RuntimeError):
    """四类原件没有齐。"""


class OverUsed(RuntimeError):
    """本批次耗用超过发票剩余可用数量。"""


def remaining(invoiced_total: object, used_amounts: List[object]) -> str:
    """remaining = invoiced_total - sum(used_by_batch)，Decimal 计算。"""
    total = Decimal(str(invoiced_total))
    used = sum((Decimal(str(value)) for value in used_amounts), Decimal("0"))
    value = total - used
    return format(value.quantize(Decimal("0.01")), "f")


@dataclass
class InvoiceUsage:
    invoice_no: str
    invoiced_total: str
    used: List[Dict[str, str]] = field(default_factory=list)

    def use(self, batch_no: str, qty: object) -> str:
        used_now = Decimal(str(qty))
        if used_now <= 0:
            raise OverUsed("qty must be positive")
        left = Decimal(self.remaining())
        if used_now > left:
            raise OverUsed("batch %s wants %s but only %s left" % (batch_no, used_now, left))
        self.used.append({"batch_no": batch_no, "qty": str(used_now)})
        return self.remaining()

    def remaining(self) -> str:
        return remaining(self.invoiced_total, [row["qty"] for row in self.used])

    def recalc_stamp(self, at: str) -> str:
        """每次回填后台账写 recalc_at。"""
        raw = "%s|%s|%s" % (self.invoice_no, self.remaining(), at)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def consistency_issues(invoice: Dict[str, Any], batch: Dict[str, Any]) -> List[Issue]:
    """发票类型/号码/品名/数量/规格/计量单位六项一致性。"""
    pairs = (
        ("invoice_type", "类型"),
        ("invoice_no", "号码"),
        ("product_name", "品名"),
        ("quantity", "数量"),
        ("spec", "规格"),
        ("unit", "计量单位"),
    )
    out: List[Issue] = []
    for key, label in pairs:
        left = str(invoice.get(key, "")).strip()
        right = str(batch.get(key, "")).strip()
        if left != right:
            out.append(
                Issue(
                    code="invoice_inconsistent",
                    batch_no=str(batch.get("batch_no", "")),
                    column=label,
                    message="发票%s与批次不一致：%r vs %r" % (label, left, right),
                )
            )
    return out


class ArchiveStore:
    """一个批次一个目录，四类原件各一份，归档后生成共享链接。"""

    def __init__(self, root: str, link_base: str = "https://alidocs.dingtalk.com/i/nodes") -> None:
        self.root = root
        self.link_base = link_base
        self.entries: Dict[str, Dict[str, str]] = {}

    def put(self, batch_no: str, kind: str, filename: str, content: bytes = b"") -> str:
        if kind not in ARCHIVE_KINDS:
            raise ValueError("unknown archive kind %r" % (kind,))
        folder = os.path.join(self.root, batch_no)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, filename)
        with open(path, "wb") as handle:
            handle.write(content)
        self.entries.setdefault(batch_no, {})[kind] = path
        return path

    def missing(self, batch_no: str) -> List[str]:
        have = self.entries.get(batch_no, {})
        return [kind for kind in ARCHIVE_KINDS if kind not in have]

    def link(self, batch_no: str) -> str:
        missing = self.missing(batch_no)
        if missing:
            raise ArchiveIncomplete("batch %s missing %s" % (batch_no, ",".join(missing)))
        return "%s/%s" % (self.link_base, batch_no)

class ArchiveIndex:
    """只登记四类原件是否到位并生成共享链接，不落盘（原件由归档目录持有）。"""

    def __init__(self, link_base: str = "https://alidocs.dingtalk.com/i/nodes") -> None:
        self.link_base = link_base
        self.entries: Dict[str, Dict[str, str]] = {}

    def put(self, batch_no: str, kind: str, filename: str) -> str:
        if kind not in ARCHIVE_KINDS:
            raise ValueError("unknown archive kind %r" % (kind,))
        self.entries.setdefault(batch_no, {})[kind] = filename
        return filename

    def missing(self, batch_no: str) -> List[str]:
        have = self.entries.get(batch_no, {})
        return [kind for kind in ARCHIVE_KINDS if kind not in have]

    def link(self, batch_no: str) -> str:
        missing = self.missing(batch_no)
        if missing:
            raise ArchiveIncomplete("batch %s missing %s" % (batch_no, ",".join(missing)))
        return "%s/%s" % (self.link_base, batch_no)