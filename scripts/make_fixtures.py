"""生成 50 个虚构批次的源数据（合成数据，不含任何客户真实信息）。"""

from __future__ import annotations

import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "fixtures", "lingxing")

PRODUCTS = [
    {"id": 1, "sku": "SKU-1001", "declare_name": "桌面收纳盒", "model": "M-1001", "material": "ABS 塑料",
     "usage": "家居收纳", "declare_unit": "个", "origin_place": "深圳", "hscode": "3924900000",
     "category": "家居", "purchase_price": "12.80"},
    {"id": 2, "sku": "SKU-1002", "declare_name": "桌面笔筒", "model": "M-1002", "material": "PP 塑料",
     "usage": "办公文具", "declare_unit": "个", "origin_place": "东莞", "hscode": "3926100000",
     "category": "家居", "purchase_price": "6.40"},
    {"id": 3, "sku": "SKU-1003", "declare_name": "文具套装", "model": "M-1003", "material": "纸与塑料",
     "usage": "学生文具", "declare_unit": "套", "origin_place": "宁波", "hscode": "9608100000",
     "category": "文具", "purchase_price": "9.90"},
    {"id": 4, "sku": "SKU-1004", "declare_name": "礼品卡套", "model": "M-1004", "material": "牛皮纸",
     "usage": "礼品包装", "declare_unit": "个", "origin_place": "广州", "hscode": "4819200000",
     "category": "礼品", "purchase_price": "3.20"},
    {"id": 5, "sku": "SKU-1005", "declare_name": "文具套装", "model": "M-1005", "material": "金属与塑料",
     "usage": "学生文具", "declare_unit": "套", "origin_place": "宁波", "hscode": "9608100000",
     "category": "文具", "purchase_price": "4.10"},
    {"id": 6, "sku": "SKU-1006", "declare_name": "硅胶隔热垫", "model": "M-1006", "material": "硅胶",
     "usage": "厨房用品", "declare_unit": "个", "origin_place": "汕头", "hscode": "",
     "category": "家居", "purchase_price": "2.60"},
    {"id": 7, "sku": "SKU-1007", "declare_name": "帆布笔袋", "model": "", "material": "帆布",
     "usage": "学生文具", "declare_unit": "个", "origin_place": "义乌", "hscode": "4202320000",
     "category": "文具", "purchase_price": "5.30"},
    {"id": 8, "sku": "BND-2S", "declare_name": "桌面组合套装A", "model": "BND-2S", "material": "ABS 塑料与 PP 塑料",
     "usage": "家居收纳", "declare_unit": "套", "origin_place": "深圳", "hscode": "3924900000",
     "category": "家居", "purchase_price": "31.90"},
    {"id": 9, "sku": "BND-3S", "declare_name": "桌面组合套装B", "model": "BND-3S", "material": "ABS 与 PP 与纸",
     "usage": "家居收纳", "declare_unit": "套", "origin_place": "深圳", "hscode": "3924900000",
     "category": "家居", "purchase_price": "29.20"},
    {"id": 10, "sku": "BND-GIFT", "declare_name": "礼品组合", "model": "BND-GIFT", "material": "PP 塑料与牛皮纸",
     "usage": "礼品包装", "declare_unit": "套", "origin_place": "广州", "hscode": "3926100000",
     "category": "礼品", "purchase_price": "9.60"},
    {"id": 11, "sku": "BND-ALT", "declare_name": "替代料组合", "model": "BND-ALT", "material": "纸与金属",
     "usage": "学生文具", "declare_unit": "套", "origin_place": "宁波", "hscode": "9608100000",
     "category": "文具", "purchase_price": "14.00"},
]

SUPPLIERS = [
    {"id": "S01", "name": "深圳宝安收纳制品厂", "contact": "李工", "tax_no": "91440300MA5F1001XA"},
    {"id": "S02", "name": "东莞文具制造有限公司", "contact": "王工", "tax_no": "91441900MA5F1002XB"},
    {"id": "S03", "name": "义乌礼品包装厂", "contact": "赵工", "tax_no": "91330782MA5F1003XC"},
]

PLAIN = ["SKU-1001", "SKU-1002", "SKU-1003", "SKU-1004"]
BUNDLES = ["BND-2S", "BND-3S", "BND-GIFT", "BND-ALT"]


def main() -> int:
    rng = random.Random(20261123)
    batches = []
    for index in range(1, 51):
        batch_no = "B2026-%04d" % index
        line_count = rng.choice([1, 1, 2, 2, 3])
        lines = []
        for line_index in range(line_count):
            if index % 7 == 0 and line_index == 0:
                sku = rng.choice(BUNDLES)
            elif index % 11 == 0 and line_index == 0:
                sku = "SKU-1006"
            elif index % 13 == 0 and line_index == 0:
                sku = "SKU-1007"
            else:
                sku = rng.choice(PLAIN)
            qty = rng.choice([2, 3, 5, 8, 12, 20])
            lines.append({
                "sku": sku,
                "qty": qty,
                "cartons": rng.choice([1, 2, 3, 5]) * 10 + rng.choice([0, 4]),
                "weight_kg": "%0.2f" % (qty * rng.choice([0.31, 0.42, 0.55])),
                "supplier_id": rng.choice(["S01", "S02", "S03"]),
                "purchase_qty": qty,
                "shipment_qty": qty,
            })
        batches.append({
            "batch_no": batch_no,
            "shipment_no": "FBA-SF-%05d" % (900 + index),
            "contract_no": "HT-2026-%04d" % index,
            "channel": "FBA-US" if index % 2 else "FBA-UK",
            "marketplace": "亚马逊美国站" if index % 2 else "亚马逊英国站",
            "lines": lines,
        })
    os.makedirs(OUT, exist_ok=True)
    for name, payload in (("products.json", PRODUCTS), ("suppliers.json", SUPPLIERS), ("batches.json", batches)):
        path = os.path.join(OUT, name)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    total_lines = sum(len(b["lines"]) for b in batches)
    print("batches %d lines %d products %d suppliers %d" % (len(batches), total_lines, len(PRODUCTS), len(SUPPLIERS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())