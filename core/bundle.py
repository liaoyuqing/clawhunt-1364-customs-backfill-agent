"""组合产品拆子 SKU：拆分表在 rules/bundle.yaml，含赠品与替代料标记。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List

from .units import quantity_text


@dataclass(frozen=True)
class BundleLine:
    sku: str
    qty: str
    gift: bool = False
    alt_of: str = ""


def _table(cfg: Dict) -> Dict:
    """既接受 rules/bundle.yaml 整份，也接受其中的 bundles 映射。"""
    if "bundles" in cfg and isinstance(cfg.get("bundles"), dict):
        return dict(cfg["bundles"])
    return dict(cfg)


def is_bundle(sku: str, cfg: Dict) -> bool:
    return sku in _table(cfg)


def split(sku: str, qty: object, cfg: Dict) -> List[BundleLine]:
    """非组合返回自身一行；组合按子件与每套用量展开。"""
    bundles = _table(cfg)
    if sku not in bundles:
        return [BundleLine(sku=sku, qty=quantity_text(qty))]
    spec = bundles[sku]
    lines: List[BundleLine] = []
    for child in spec.get("children", []):
        child_qty = Decimal(str(qty)) * Decimal(str(child["qty"]))
        lines.append(
            BundleLine(
                sku=str(child["sku"]),
                qty=quantity_text(child_qty),
                gift=bool(child.get("gift", False) or spec.get("gift", False)),
                alt_of=str(child.get("alt_of", "")),
            )
        )
    return lines


def mark(sku: str, cfg: Dict) -> str:
    """台账「组合标记」列的值。"""
    if not is_bundle(sku, cfg):
        return ""
    return "组合:%s" % sku