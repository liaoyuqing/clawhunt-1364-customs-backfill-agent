"""独立参考实现：只按客户规则文字重算预期值，刻意不复用 core/ 任何代码。

存在的意义是做交叉核对：抽检脚本拿它算出的期望值与回填引擎的实际值逐格比对，
两套代码独立写出，同时错成一样的概率极低。
"""

from __future__ import annotations

import json
import os
import sys
from fractions import Fraction

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_rules():
    out = {}
    for name in ("bundle", "unit", "brand_words", "declare_price", "required_fields", "invoice_companies", "ledger_columns"):
        with open(os.path.join(ROOT, "rules", name + ".yaml"), encoding="utf-8") as handle:
            out[name] = yaml.safe_load(handle)
    return out


def half_up(value: Fraction, digits: int) -> str:
    """四舍五入到指定小数位，用整数运算避免浮点误差。"""
    scale = 10 ** digits
    scaled = value * scale
    sign = -1 if scaled < 0 else 1
    scaled = abs(scaled)
    whole = scaled.numerator // scaled.denominator
    rem = scaled - whole
    if rem >= Fraction(1, 2):
        whole += 1
    result = Fraction(sign * whole, scale)
    text = ("%." + str(digits) + "f") % float(result)
    return text


def qty_text(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    return half_up(value, 2)


def split_sku(sku, qty, bundles):
    """组合按 children 展开，非组合原样返回。"""
    spec = bundles.get(sku)
    if spec is None:
        return [(sku, qty, "", False)]
    out = []
    for child in spec["children"]:
        line_qty = qty * Fraction(str(child["qty"]))
        alt = str(child.get("alt_of", ""))
        gift = bool(child.get("gift", False)) or bool(spec.get("gift", False))
        out.append((str(child["sku"]), line_qty, alt, gift))
    return out


def product_fields(product):
    return {
        "产品中文报关名": str(product.get("declare_name", "")),
        "型号": str(product.get("model", "")),
        "材质": str(product.get("material", "")),
        "用途": str(product.get("usage", "")),
        "报关单位": str(product.get("declare_unit", "")),
        "境内货源地": str(product.get("origin_place", "")),
        "HSCODE": str(product.get("hscode", "")),
        "SKU": str(product.get("sku", "")),
    }


def price_for(product, rules):
    cfg = rules["declare_price"]
    category = str(product.get("category", ""))
    ratio = cfg.get("by_category", {}).get(category, cfg["default_ratio"])
    value = Fraction(str(product.get("purchase_price", 0))) * Fraction(str(ratio))
    return half_up(value, int(cfg.get("rounding", 2)))


SOURCE_ERROR_Hscode = "missing_hscode"


def build_expected(batches, products, suppliers, rules):
    """返回 {row_key: {column: {"expected": str, "decidable": bool, "reason": str}}}。"""
    expected = {}
    source_errors = []
    for batch in batches:
        batch_no = str(batch["batch_no"])
        head = {
            "批次号": {"expected": batch_no, "decidable": True, "reason": ""},
            "发货单号": {"expected": str(batch.get("shipment_no", "")), "decidable": True, "reason": ""},
            "合同号": {"expected": str(batch.get("contract_no", "")), "decidable": True, "reason": ""},
        }
        expected[batch_no] = head
        seq = 0
        for line in batch["lines"]:
            sku = str(line["sku"])
            product = products[sku]
            parts = split_sku(sku, Fraction(str(line["qty"])), rules["bundle"]["bundles"])
            for child_sku, child_qty, alt, gift in parts:
                child = products.get(child_sku, product)
                row_key = "%s#%d" % (batch_no, seq)
                seq += 1
                fields = product_fields(child)
                cells = {
                    "批次号": {"expected": batch_no, "decidable": True, "reason": ""},
                    "发货单号": {"expected": str(batch.get("shipment_no", "")), "decidable": True, "reason": ""},
                    "合同号": {"expected": str(batch.get("contract_no", "")), "decidable": True, "reason": ""},
                }
                for column, value in fields.items():
                    decidable = True
                    reason = ""
                    if column == "HSCODE" and not value:
                        decidable = False
                        reason = SOURCE_ERROR_Hscode
                        source_errors.append({"row_key": row_key, "column": column, "reason": reason})
                    cells[column] = {"expected": value, "decidable": decidable, "reason": reason}
                model = fields["型号"]
                if gift:
                    model = model + "|赠品"
                if alt:
                    model = model + "|替代:" + alt
                cells["型号"] = {"expected": model, "decidable": bool(model), "reason": "" if model else "missing_model"}
                parent_is_bundle = sku in rules["bundle"]["bundles"]
                qty_value = qty_text(child_qty)
                cells["数量"] = {"expected": qty_value, "decidable": True, "reason": "bundle_split" if parent_is_bundle else ""}
                cells["箱数"] = {"expected": str(line.get("cartons", "")), "decidable": True, "reason": ""}
                cells["重量KG"] = {"expected": str(line.get("weight_kg", "")), "decidable": True, "reason": ""}
                supplier = suppliers.get(str(line.get("supplier_id")), {})
                cells["供应商"] = {"expected": str(supplier.get("name", "")), "decidable": bool(supplier.get("name")), "reason": "" if supplier.get("name") else "missing_supplier"}
                cells["报关单价"] = {"expected": price_for(child, rules), "decidable": True, "reason": ""}
                expected[row_key] = cells
    return expected, source_errors


def main() -> int:
    rules = load_rules()
    fixtures = os.path.join(ROOT, "fixtures", "lingxing")
    with open(os.path.join(fixtures, "batches.json"), encoding="utf-8") as handle:
        batches = json.load(handle)
    with open(os.path.join(fixtures, "products.json"), encoding="utf-8") as handle:
        products = {item["sku"]: item for item in json.load(handle)}
    with open(os.path.join(fixtures, "suppliers.json"), encoding="utf-8") as handle:
        suppliers = {item["id"]: item for item in json.load(handle)}
    expected, source_errors = build_expected(batches, products, suppliers, rules)
    payload = {
        "rule_versions": {name: str(doc.get("version")) for name, doc in rules.items()},
        "expected": expected,
        "source_errors": source_errors,
        "rows": len(expected),
        "cells": sum(len(cells) for cells in expected.values()),
    }
    out = os.path.join(ROOT, "fixtures", "expected.json")
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
    print("rows %d cells %d source_errors %d -> %s" % (payload["rows"], payload["cells"], len(source_errors), out))
    return 0


if __name__ == "__main__":
    sys.exit(main())