"""模块四：发票号/开票日期/耗用/剩余/结余回填，四类原件归档链接回填。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .archive import (
    ARCHIVE_KINDS,
    ArchiveIncomplete,
    ArchiveIndex,
    ArchiveStore,
    InvoiceUsage,
    consistency_issues,
)
from .config import Rules
from .ledger import Ledger
from .models import Issue, SourceRef

AT = "2026-11-23T00:00:00+08:00"

INVOICE_COLUMNS = (
    "开票公司",
    "发票号",
    "开票日期",
    "耗用发票数量",
    "发票剩余可用数量",
    "结余",
    "归档共享链接",
)


class InvoiceLedgerError(RuntimeError):
    """发票台账缺某个品类，或同一品类配了多张发票。"""


@dataclass
class InvoiceLedger:
    """发票台账：一个品类一张发票，票面数量即可用总数量。"""

    records: Sequence[Dict[str, Any]]
    at: str = AT

    def __post_init__(self) -> None:
        self._by_category: Dict[str, Dict[str, Any]] = {}
        self._usage: Dict[str, InvoiceUsage] = {}
        for row in self.records:
            category = str(row.get("category", "")).strip()
            invoice_no = str(row.get("invoice_no", "")).strip()
            capacity = str(row.get("invoiced_qty", "")).strip()
            if not category or not invoice_no or not capacity:
                raise InvoiceLedgerError("发票台账缺 category / invoice_no / invoiced_qty")
            if category in self._by_category:
                raise InvoiceLedgerError("品类 %s 配了多张发票，本回放只支持单发票" % category)
            self._by_category[category] = dict(row)
            self._usage[invoice_no] = InvoiceUsage(invoice_no=invoice_no, invoiced_total=capacity)

    def categories(self) -> List[str]:
        return sorted(self._by_category)

    def pick(self, category: str) -> Dict[str, Any]:
        if category not in self._by_category:
            raise InvoiceLedgerError("品类 %s 没有可用发票" % category)
        return self._by_category[category]

    def new_usage(self, category: str) -> InvoiceUsage:
        record = self.pick(category)
        return InvoiceUsage(invoice_no=str(record["invoice_no"]), invoiced_total=str(record["invoiced_qty"]))

    def usage_of(self, category: str) -> InvoiceUsage:
        return self._usage[str(self.pick(category)["invoice_no"])]

    def snapshot(self) -> List[Dict[str, str]]:
        out = []
        for category in self.categories():
            record = self.pick(category)
            usage = self.usage_of(category)
            out.append(
                {
                    "category": category,
                    "invoice_no": str(record["invoice_no"]),
                    "invoiced_qty": str(record["invoiced_qty"]),
                    "used": str(sum(_dec(row["qty"]) for row in usage.used)),
                    "remaining": usage.remaining(),
                    "stamp": usage.recalc_stamp(self.at),
                }
            )
        return out


def _dec(value: object):
    from decimal import Decimal

    return Decimal(str(value))


@dataclass
class Module4Result:
    batch_no: str
    rows: List[str] = field(default_factory=list)
    categories: List[str] = field(default_factory=list)
    link: str = ""
    issues: List[Issue] = field(default_factory=list)
    local_model: List[Dict[str, Any]] = field(default_factory=list)


class Module4Filler:
    """把发票与归档结果写回台账后 7 列，结余与剩余按行实时重算。

    两个口径刻意分开：
    - 「发票剩余可用数量」= 票面数量 − 全部已回填批次累计耗用（跨批次共享同一张发票）；
    - 「结余」= 票面数量 − 本批次累计耗用（本批次视角的余量）。
    两者都由 core.archive.remaining() 计算，不允许在原地另写一套减法。
    """

    def __init__(
        self,
        rules: Rules,
        invoices: InvoiceLedger,
        archive_root: Optional[str] = None,
        at: str = AT,
        local_model: Optional[Any] = None,
    ) -> None:
        self.rules = rules
        self.invoices = invoices
        self.archive_root = archive_root
        self.at = at
        self.local_model = local_model
        self.cfg_invoice = SourceRef(kind="config", ref="rules/invoice_companies.yaml", at=at)
        self.api_invoice = SourceRef(kind="api", ref="invoice_ledger/invoices", at=at)
        self.api_archive = SourceRef(kind="api", ref="dingtalk/drive/archive", at=at)
        self._stores: Dict[str, Any] = {}
        self._local: Dict[Tuple[str, str], InvoiceUsage] = {}
        self._issues: List[Issue] = []

    @staticmethod
    def _put(ledger: Ledger, row_key: str, column: str, value: object, source: SourceRef) -> bool:
        """只在格子还空着时写入：视觉补过的格不覆盖（否则会触发 DuplicateWrite）。"""
        if ledger.row(row_key).get(column) is not None:
            return False
        ledger.write(row_key, column, value, source)
        return True

    @property
    def issues(self) -> List[Issue]:
        """本地模型识别阶段报出的问题（与 fill_batch 的 issues 分开）。"""
        return list(self._issues)

    def _store(self, batch_no: str):
        """归档：给了目录就落盘（四类原件各一份），否则只登记并生成链接。"""
        if batch_no not in self._stores:
            if self.archive_root:
                store = ArchiveStore(os.path.join(self.archive_root, batch_no))
                for kind in ARCHIVE_KINDS:
                    store.put(batch_no, kind, "%s-%s.pdf" % (batch_no, kind), b"")
            else:
                store = ArchiveIndex()
                for kind in ARCHIVE_KINDS:
                    store.put(batch_no, kind, "%s-%s.pdf" % (batch_no, kind))
            self._stores[batch_no] = store
        return self._stores[batch_no]

    def _local_usage(self, batch_no: str, category: str) -> InvoiceUsage:
        key = (batch_no, category)
        if key not in self._local:
            self._local[key] = self.invoices.new_usage(category)
        return self._local[key]

    def fill_batch(self, ledger: Ledger, batch_no: str, rows: Sequence[Dict[str, str]]) -> Module4Result:
        """rows: [{row_key, category}]，按行写开票公司/发票号/耗用/剩余/结余。"""
        result = Module4Result(batch_no=batch_no)
        store = self._store(batch_no)
        try:
            result.link = store.link(batch_no)
        except ArchiveIncomplete as exc:
            result.issues.append(
                Issue(code="archive_incomplete", batch_no=batch_no, column="归档共享链接", message=str(exc))
            )
        if result.link:
            self._put(ledger, batch_no, "归档共享链接", result.link, self.api_archive)
        seen: List[str] = []
        for item in rows:
            row_key = str(item["row_key"])
            category = str(item["category"])
            row = ledger.row(row_key)
            qty = row.value("数量") or "0"
            global_usage = self.invoices.usage_of(category)
            local_usage = self._local_usage(batch_no, category)
            remaining_value = global_usage.use(batch_no, qty)
            balance_value = local_usage.use(batch_no, qty)
            record = self.invoices.pick(category)
            self._put(ledger, row_key, "开票公司", self._company_for(category), self.cfg_invoice)
            """接了本地模型时，发票号与开票日期留给本地模型识别后再写；
            没接就按台账里的值写，行为与原来一致。"""
            if self.local_model is None:
                self._put(ledger, row_key, "发票号", str(record["invoice_no"]), self.api_invoice)
                self._put(ledger, row_key, "开票日期", str(record.get("issued_at", "")), self.api_invoice)
            self._put(ledger, row_key, "耗用发票数量", qty, self.api_invoice)
            self._put(ledger, row_key, "发票剩余可用数量", remaining_value, self.api_invoice)
            self._put(ledger, row_key, "结余", balance_value, self.api_invoice)
            if result.link:
                self._put(ledger, row_key, "归档共享链接", result.link, self.api_archive)
            result.rows.append(row_key)
            if category not in seen:
                seen.append(category)
        result.categories = seen
        return result

    def recognize_invoices(self, ledger: Ledger, items: Sequence[Dict[str, str]]) -> List[Dict[str, Any]]:
        """用本地模型识别发票原件，把识别到的发票号与发票台账核对。

        items: [{row_key, category, image_url}]，image_url 可以是 data URI 或平台 file id。
        识别到的发票号与台账不一致时不写台账（发票号留空进待补清单），只报一条
        local_model_invoice_mismatch；一致才把识别结果写回对应行。
        """
        if self.local_model is None:
            return []
        out: List[Dict[str, Any]] = []
        for item in items:
            row_key = str(item["row_key"])
            category = str(item.get("category", ""))
            image_url = str(item.get("image_url", ""))
            try:
                recognized = self.local_model.recognize_invoice(image_url)
            except Exception as exc:
                """本地模型不可用或地址未过内网校验：不写台账，报一条问题。"""
                out.append({
                    "row_key": row_key,
                    "recognized": {},
                    "error": str(exc),
                })
                self._issues.append(
                    Issue(
                        code="local_model_unavailable",
                        batch_no=ledger.row(row_key).batch_no,
                        column="发票号",
                        message="本地模型识别失败：%s" % exc,
                    )
                )
                continue
            expected = ""
            try:
                expected = str(self.invoices.pick(category)["invoice_no"])
            except InvoiceLedgerError:
                expected = ""
            actual = str(recognized.get("invoice_no", ""))
            matched = bool(expected) and actual == expected
            if not matched:
                self._issues.append(
                    Issue(
                        code="local_model_invoice_mismatch",
                        batch_no=ledger.row(row_key).batch_no,
                        column="发票号",
                        message="本地模型识别到发票号 %s，与台账 %s 不一致，发票号留空进待补" % (actual, expected),
                    )
                )
            else:
                row = ledger.row(row_key)
                if row.get("发票号") is None:
                    ledger.write(row_key, "发票号", actual, self.api_invoice)
                for column in ("开票日期",):
                    value = str(recognized.get(column, ""))
                    if value and row.get(column) is None:
                        ledger.write(row_key, column, value, self.api_invoice)
            out.append({
                "row_key": row_key,
                "recognized": dict(recognized),
                "expected_invoice_no": expected,
                "matched": matched,
            })
        return out

    def _company_for(self, category: str) -> str:
        table = self.rules.invoice_companies.get("by_category", {}) or {}
        entry = table.get(category)
        if entry is None:
            raise InvoiceLedgerError("品类 %s 没有开票公司配置" % (category,))
        return str(entry["company"])


def category_of(products: Dict[str, Dict[str, Any]], sku: str) -> str:
    item = products.get(sku) or {}
    return str(item.get("category", ""))


def invoice_consistency(invoices: InvoiceLedger, category: str, batch: Dict[str, Any]) -> List[Issue]:
    """发票与批次六项一致性（类型/号码/品名/数量/规格/计量单位）。"""
    record = invoices.pick(category)
    view = {
        "invoice_type": record.get("invoice_type", ""),
        "invoice_no": record.get("invoice_no", ""),
        "product_name": record.get("product_name", ""),
        "quantity": record.get("quantity", ""),
        "spec": record.get("spec", ""),
        "unit": record.get("unit", ""),
    }
    return consistency_issues(view, batch)