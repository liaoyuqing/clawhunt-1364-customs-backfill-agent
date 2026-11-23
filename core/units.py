"""单位换算：套/箱/个之间的系数全部来自 rules/unit.yaml。"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Dict


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


def quantity_text(value: object) -> str:
    """数量文本：整数不带小数点，非整数保留两位。"""
    dec = Decimal(str(value))
    if dec == dec.to_integral_value():
        return str(int(dec))
    return format(dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")