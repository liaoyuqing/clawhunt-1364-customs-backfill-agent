"""申报单价：按品类系数换算，两位小数，四舍五入。"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Dict


def ratio_for(category: str, cfg: Dict) -> Decimal:
    by_category = cfg.get("by_category", {}) or {}
    raw = by_category.get(category, cfg.get("default_ratio"))
    if raw is None:
        raise KeyError("no declare price ratio for category %r" % (category,))
    return Decimal(str(raw))


def declare_price(purchase_price: object, category: str, cfg: Dict) -> str:
    """采购单价 × 品类系数，保留配置位数的小数。"""
    scale = int(cfg.get("rounding", 2))
    exp = Decimal(1).scaleb(-scale)
    value = (Decimal(str(purchase_price)) * ratio_for(category, cfg)).quantize(exp, rounding=ROUND_HALF_UP)
    return format(value, "f")