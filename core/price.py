"""申报单价：按品类系数换算，折算单位时同除系数，四位小数去尾零。"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Dict


def ratio_for(category: str, cfg: Dict) -> Decimal:
    by_category = cfg.get("by_category", {}) or {}
    raw = by_category.get(category, cfg.get("default_ratio"))
    if raw is None:
        raise KeyError("no declare price ratio for category %r" % (category,))
    return Decimal(str(raw))


def trim(value: Decimal, min_decimals: int) -> str:
    text = format(value, "f")
    whole, sep, frac = text.partition(".")
    if not sep:
        return whole + "." + ("0" * min_decimals)
    frac = frac.rstrip("0")
    while len(frac) < min_decimals:
        frac = frac + "0"
    return whole + "." + frac


def declare_price(purchase_price: object, category: str, cfg: Dict, scale: object = 1) -> str:
    """采购单价 × 品类系数 ÷ 单位折算系数，保留配置位数后去掉尾零。"""
    scale_value = Decimal(str(scale)) if scale is not None else Decimal("1")
    if scale_value <= 0:
        raise ValueError("unit scale must be positive")
    digits = int(cfg.get("rounding", 2))
    exp = Decimal(1).scaleb(-digits)
    value = Decimal(str(purchase_price)) * ratio_for(category, cfg) / scale_value
    return trim(value.quantize(exp, rounding=ROUND_HALF_UP), int(cfg.get("min_decimals", 2)))