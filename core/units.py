"""单位换算：套/箱/个之间的系数全部来自 rules/unit.yaml。"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Tuple


class UnitNotConfigured(KeyError):
    """换算路径不在配置表里。"""


def _factors(cfg: Dict) -> Dict:
    """既接受 unit.yaml 整份，也接受已经剥好的 factors 映射。"""
    inner = cfg.get("factors")
    if isinstance(inner, dict):
        return dict(inner)
    return dict(cfg)


def factor(src: str, dst: str, cfg: Dict) -> Decimal:
    key = "%s->%s" % (src, dst)
    factors = _factors(cfg)
    if key not in factors:
        raise UnitNotConfigured(key)
    return Decimal(str(factors[key]))


def convert(qty: object, src: str, dst: str, cfg: Dict) -> Decimal:
    return Decimal(str(qty)) * factor(src, dst, cfg)


def base_unit(cfg: Dict) -> str:
    """申报用的基础单位，缺省按「个」。"""
    return str(cfg.get("base_unit") or "个")


def to_base_unit(qty: object, src: str, cfg: Dict) -> Tuple[Decimal, Decimal]:
    """把数量折算到基础单位，返回 (折算后数量, 系数)。已同单位时系数为 1。

    系数缺失时抛 UnitNotConfigured，调用方负责转成一条勾稽问题，而不是静默按原单位写入。
    """
    dst = base_unit(cfg)
    if str(src) == dst:
        return Decimal(str(qty)), Decimal("1")
    scale = factor(str(src), dst, cfg)
    return Decimal(str(qty)) * scale, scale


def scale_price(price: object, scale: Decimal, cfg: Dict) -> str:
    """单价随单位折算：单价 ÷ 系数，保留配置位数，四舍五入。"""
    digits = int((cfg.get("declaration") or {}).get("rounding") or cfg.get("rounding_scale") or 2)
    exp = Decimal(1).scaleb(-digits)
    value = (Decimal(str(price)) / Decimal(scale)).quantize(exp, rounding=ROUND_HALF_UP)
    return format(value, "f")


def unit_note(src: str, qty: object, converted: Decimal, cfg: Dict) -> str:
    """换算留痕：原文写进 reports/unit_conversions.json，台账列序不为此改动。"""
    if str(src) == base_unit(cfg):
        return ""
    return "%s %s%s -> %s%s" % (qty, src, "", quantity_text(converted), base_unit(cfg))


def quantity_text(value: object) -> str:
    """数量文本：整数不带小数点，非整数保留两位。"""
    dec = Decimal(str(value))
    if dec == dec.to_integral_value():
        return str(int(dec))
    return format(dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")