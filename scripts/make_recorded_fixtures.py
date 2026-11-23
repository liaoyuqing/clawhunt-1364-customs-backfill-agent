"""生成 fixtures/recorded/ 下的本地合成录制响应（可重放，不是厂商接口的真实抓包）。

用途：让抽检脚本与冒烟脚本在没有真实凭据时也能跑通同一条调用路径与解析逻辑。
每个文件都写明来源标注，接贵方账号后用 scripts/recorded_smoke.py --source live 拉真实数据做同样断言。
"""

from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FIXTURES = os.path.join(ROOT, "fixtures", "lingxing")
RECORDED = os.path.join(ROOT, "fixtures", "recorded")

PROVENANCE = "local synthetic replay, not a capture from the vendor API"
NOTE = (
    "以下为本地合成的可重放响应，用于证明调用路径与解析逻辑正确；"
    "接贵方账号后用 scripts/recorded_smoke.py --source live 拉真实数据做同样断言"
)


def load(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return json.load(handle)


def page(items, total=None):
    """开放平台列表响应形状：{code, msg, data:{list, total}}。"""
    rows = []
    for index, item in enumerate(items):
        row = dict(item)
        row.setdefault("id", index + 1)
        row["_provenance"] = PROVENANCE
        rows.append(row)
    return {
        "_provenance": PROVENANCE,
        "_note": NOTE,
        "code": 0,
        "msg": "synthetic replay",
        "data": {"list": rows, "total": len(rows) if total is None else int(total)},
    }


def dump(name, payload):
    os.makedirs(RECORDED, exist_ok=True)
    path = os.path.join(RECORDED, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    return path


def main() -> int:
    batches = load("batches.json")
    products = load("products.json")
    suppliers = load("suppliers.json")
    out = [
        dump("lingxing_batches_page1.SYNTHETIC.json", page(batches)),
        dump("lingxing_products_page1.SYNTHETIC.json", page(products)),
        dump("lingxing_suppliers_page1.SYNTHETIC.json", page(suppliers)),
    ]
    for path in out:
        print("wrote %s" % os.path.relpath(path, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
