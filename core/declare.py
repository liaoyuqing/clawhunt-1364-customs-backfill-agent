"""模块二：批次回填。接口优先、视觉补位，每一格都带来源。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import bundle as bundle_mod
from .checks import all_issues, pending_list
from .config import Rules
from .ledger import Ledger
from .lingxing import product_declare_fields
from .models import Issue, Row, SourceRef
from .price import declare_price

AT = "2026-11-23T00:00:00+08:00"


class MissingSource(RuntimeError):
    """源系统里找不到该批次需要的记录。"""


@dataclass
class FillResult:
    batch_no: str
    row_keys: List[str] = field(default_factory=list)
    rows: List[Row] = field(default_factory=list)
    issues: List[Issue] = field(default_factory=list)

    def pending(self) -> List[Dict[str, str]]:
        return pending_list(self.issues)


@dataclass(frozen=True)
class BatchContext:
    """一个批次回填所需的四类来源，全部来自领星或配置。"""

    batch: Dict[str, Any]
    products: Dict[str, Dict[str, Any]]
    suppliers: Dict[str, Dict[str, Any]]
    vision: Dict[str, Dict[str, Any]] = field(default_factory=dict)


class BatchFiller:
    """批次号进来，台账行出去。组合拆分、单位换算、单价换算都读配置。"""

    def __init__(self, rules: Rules, at: str = AT) -> None:
        self.rules = rules
        self.at = at
        self.api = SourceRef(kind="api", ref="lingxing/products+purchase+fba_shipment", at=at)
        self.cfg_price = SourceRef(kind="config", ref="rules/declare_price.yaml", at=at)
        self.cfg_unit = SourceRef(kind="config", ref="rules/unit.yaml", at=at)
        self.cfg_bundle = SourceRef(kind="config", ref="rules/bundle.yaml", at=at)

    def fill(self, ledger: Ledger, ctx: BatchContext) -> FillResult:
        batch = ctx.batch
        batch_no = str(batch["batch_no"])
        ledger.ensure_row(batch_no, batch_no)
        ledger.write(batch_no, "批次号", batch_no, self.api)
        ledger.write(batch_no, "发货单号", str(batch.get("shipment_no", "")), self.api)
        ledger.write(batch_no, "合同号", str(batch.get("contract_no", "")), self.api)

        result = FillResult(batch_no=batch_no, row_keys=[batch_no])
        self._seq = 0
        for line in batch.get("lines", []):
            self._fill_line(ledger, result, ctx, line)
        for name, payload in ctx.vision.items():
            target = name[len("batch:"):] if name.startswith("batch:") else name
            if target != batch_no:
                continue
            self._fill_vision(ledger, result, batch_no, payload)
        return result

    def _fill_line(self, ledger: Ledger, result: FillResult, ctx: BatchContext, line: Dict[str, Any]) -> None:
        sku = str(line["sku"])
        product = ctx.products.get(sku)
        if product is None:
            raise MissingSource("product %s not found in lingxing" % sku)
        parent_category = str(product.get("category", ""))
        parent_price = product.get("purchase_price", 0)
        parts = bundle_mod.split(sku, line["qty"], self.rules.bundles)
        for part in parts:
            child_sku = part.sku
            child = ctx.products.get(child_sku, product)
            fields = product_declare_fields(child)
            row_key = "%s#%d" % (result.batch_no, self._seq)
            self._seq += 1
            ledger.ensure_row(row_key, result.batch_no)
            ledger.write(row_key, "批次号", result.batch_no, self.api)
            ledger.write(row_key, "发货单号", str(ctx.batch.get("shipment_no", "")), self.api)
            ledger.write(row_key, "合同号", str(ctx.batch.get("contract_no", "")), self.api)
            for column in ("产品中文报关名", "材质", "用途", "报关单位", "境内货源地", "HSCODE", "SKU"):
                ledger.write(row_key, column, fields[column], self.api)
            model = fields["型号"]
            if part.gift:
                model = "%s%s" % (model, "|赠品")
            if part.alt_of:
                model = "%s|替代:%s" % (model, part.alt_of)
            ledger.write(row_key, "型号", model, self.cfg_bundle if (part.gift or part.alt_of) else self.api)
            ledger.write(row_key, "数量", part.qty, self.cfg_unit if bundle_mod.is_bundle(sku, self.rules.bundles) else self.api)
            ledger.write(row_key, "箱数", str(line.get("cartons", "")), self.api)
            ledger.write(row_key, "重量KG", str(line.get("weight_kg", "")), self.api)
            supplier = ctx.suppliers.get(str(line.get("supplier_id")), {})
            ledger.write(row_key, "供应商", str(supplier.get("name", "")), self.api)
            category = str(child.get("category", parent_category))
            price = child.get("purchase_price", parent_price)
            ledger.write(row_key, "报关单价", declare_price(price, category, self.rules.price_rules), self.cfg_price)
            row = ledger.row(row_key)
            result.row_keys.append(row_key)
            result.rows.append(row)
            result.issues.extend(
                all_issues(
                    row,
                    self.rules.required,
                    self.rules.hscode_pattern,
                    self.rules.reconciliation,
                    left_values={"R1": str(line.get("purchase_qty", "")), "R2": fields["报关单位"], "R3": str(ctx.batch.get("contract_no", ""))},
                    right_values={"R1": str(line.get("shipment_qty", "")), "R2": fields["报关单位"], "R3": str(ctx.batch.get("contract_no", ""))},
                )
            )

    def _fill_vision(self, ledger: Ledger, result: FillResult, row_key: str, payload: Dict[str, Any]) -> None:
        """视觉补位：置信度不足的字段不写，转人工待补清单。"""
        from .vision import ScreenshotSource

        shot = ScreenshotSource(payload)
        row = ledger.row(row_key)
        for field in shot.fields:
            if field.name not in ledger.columns:
                continue
            if field.needs_manual:
                result.issues.append(
                    Issue(
                        code="vision_low_confidence",
                        batch_no=row.batch_no,
                        column=field.name,
                        message="视觉置信度 %.2f 低于阈值，转人工" % field.confidence,
                    )
                )
                continue
            if field.name in row.cells:
                continue
            ledger.write(row_key, field.name, field.text, field.source(self.at))


def merge_batches(
    ledgers: List[Ledger],
    merge_key_columns: Tuple[str, ...] = ("产品中文报关名", "型号", "HSCODE", "境内货源地", "报关单价"),
) -> List[Dict[str, Any]]:
    """多批次合并报关：同渠道且五个关键列相同才可合并，返回分组。"""
    groups: Dict[Tuple[str, ...], List[str]] = {}
    skipped: List[str] = []
    for ledger in ledgers:
        for row in ledger.rows():
            key = tuple(row.value(column) for column in merge_key_columns)
            """五个关键列有任一为空的行（批次表头行）不参与合并判定。"""
            if any(not part.strip() for part in key):
                skipped.append(row.row_key)
                continue
            groups.setdefault(key, []).append(row.row_key)
    out: List[Dict[str, Any]] = []
    for key, keys in groups.items():
        out.append({"key": list(key), "rows": keys, "mergable": len(keys) > 1})
    out.sort(key=lambda item: (-len(item["rows"]), item["key"]))
    out.append({"key": [], "rows": skipped, "mergable": False, "skipped": True})
    return out


def partial_declare(rows: List[Row], carton_from: int, carton_to: int) -> Dict[str, Any]:
    """部分报关：按箱段切，返回标注与箱数之和。"""
    if carton_from > carton_to:
        raise ValueError("carton_from must not exceed carton_to")
    selected = []
    total = 0
    for row in rows:
        raw = row.value("箱数").strip()
        if not raw.isdigit():
            continue
        cartons = int(raw)
        if carton_from <= cartons <= carton_to:
            selected.append(row.row_key)
            total += cartons
    return {"carton_from": carton_from, "carton_to": carton_to, "rows": selected, "cartons": total}