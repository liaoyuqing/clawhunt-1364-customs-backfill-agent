"""把领星官方接口的原始返回映射成台账流水线用的内部形状（显式、可核对、不猜）。

为什么需要这一层：
- 夹具 / 录制回放用的内部形状是「批次 → lines → sku」的紧凑结构，字段名是内部命名；
- 官方接口返回的是平台自己的字段名（camelCase / snake_case 混排，嵌套 shipments 里还带 item_list）；
- 两者不能靠猜字段名对上，所以这里写一张**显式映射表**，每个内部字段都标注它来自官方哪个字段、
  哪个接口、哪一页文档。映射不到的内部字段一律留空并在 `unmapped_fields` 里列出来，
  由上层如实报「源系统没有这个字段」，绝不填假值。

关键事实（写进 docs/LIVE_EVIDENCE.md 第 6 节）：
- 产品列表接口 /erp/sc/routing/data/local_inventory/productList 的官方返回里**没有**中文报关名、
  材质、用途、型号、HSCODE、境内货源地、报关单位这些字段；它们真正的来源是
  报关资料明细 /supplyShipmentBasic/openApi/customsDeclaration/batchDetail 的 products[]，
  字段名分别为 customsNameCn / materialCn / useCn / model / productCode / domesticSource / transactionUnit。
- 因此 products 的映射是「产品列表提供 SKU、品名、品类、采购价」+「报关资料明细补齐报关字段」两段拼起来的。
- 货件列表 /erp/sc/data/fba_report/shipmentList 的 data 是对象（data.list），total 在顶层，不是数组。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "map_products",
    "map_suppliers",
    "map_batches",
    "batch_unmapped_fields",
    "PRODUCT_SOURCES",
]

# 内部字段 -> (官方字段, 接口, 文档)
PRODUCT_SOURCES: Dict[str, Tuple[str, str, str]] = {
    "sku": ("sku", "productList", "https://apidoc.lingxing.com/new/api-docs/Product/ProductLists/"),
    "declare_name": ("customsNameCn", "customsDeclaration/batchDetail", "https://apidoc.lingxing.com/new/api-docs/Logistics/CustomsDeclarationBatchDetail/"),
    "model": ("model", "customsDeclaration/batchDetail", "https://apidoc.lingxing.com/new/api-docs/Logistics/CustomsDeclarationBatchDetail/"),
    "material": ("materialCn", "customsDeclaration/batchDetail", "https://apidoc.lingxing.com/new/api-docs/Logistics/CustomsDeclarationBatchDetail/"),
    "usage": ("useCn", "customsDeclaration/batchDetail", "https://apidoc.lingxing.com/new/api-docs/Logistics/CustomsDeclarationBatchDetail/"),
    "declare_unit": ("transactionUnit", "customsDeclaration/batchDetail", "https://apidoc.lingxing.com/new/api-docs/Logistics/CustomsDeclarationBatchDetail/"),
    "origin_place": ("domesticSource", "customsDeclaration/batchDetail", "https://apidoc.lingxing.com/new/api-docs/Logistics/CustomsDeclarationBatchDetail/"),
    "hscode": ("productCode", "customsDeclaration/batchDetail", "https://apidoc.lingxing.com/new/api-docs/Logistics/CustomsDeclarationBatchDetail/"),
    "category": ("category", "customsDeclaration/batchDetail", "https://apidoc.lingxing.com/new/api-docs/Logistics/CustomsDeclarationBatchDetail/"),
    "purchase_price": ("cg_price", "productList", "https://apidoc.lingxing.com/new/api-docs/Product/ProductLists/"),
    "product_name": ("product_name", "productList", "https://apidoc.lingxing.com/new/api-docs/Product/ProductLists/"),
}

# 内部形状里、官方接口确实没有对应字段的项：不填假值，显式列出来给上层如实报告。
BATCH_UNMAPPED = {
    "channel": "领星开放接口没有「渠道」字段；货件列表只给 destination_fulfillment_center_id 与 shipping_solution",
    "marketplace": "领星开放接口没有站点中文字段；只有 sid / seller",
    "lines.weight_kg": "货件列表的 item_list 没有单行重量；重量在装箱明细 shipmentPackingList[].weight",
    "lines.purchase_qty": "采购数量在采购单接口（本轮未接入），货件侧只有 quantity_shipped",
}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _first(row: Dict[str, Any], names: Sequence[str]) -> Any:
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
    return None


def map_products(
    product_rows: Iterable[Dict[str, Any]],
    customs_detail_rows: Iterable[Dict[str, Any]] = (),
) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """产品列表 + 报关资料明细 → 内部 products（键为 SKU）。

    返回 (products, notes)。notes 记录哪些内部字段在真实返回里完全没有取到，
    上层据此如实标注，不会把空值当成"源数据就是这样"。
    """
    declared: Dict[str, Dict[str, Any]] = {}
    for declaration in customs_detail_rows or ():
        for item in declaration.get("products") or []:
            sku = _text(item.get("sku"))
            if sku and sku not in declared:
                declared[sku] = dict(item)

    products: Dict[str, Dict[str, Any]] = {}
    for index, row in enumerate(product_rows or (), start=1):
        sku = _text(_first(row, ("sku", "sku_identifier")))
        if not sku:
            continue
        detail = declared.get(sku, {})
        merged: Dict[str, Any] = {}
        merged["id"] = _first(row, ("id",)) or index
        merged["sku"] = sku
        merged["product_name"] = _text(_first(row, ("product_name", "productName")))
        merged["declare_name"] = _text(_first(detail, ("customsNameCn",))) or merged["product_name"]
        merged["model"] = _text(_first(detail, ("model",))) or _text(_first(row, ("model",)))
        merged["material"] = _text(_first(detail, ("materialCn",)))
        merged["usage"] = _text(_first(detail, ("useCn",)))
        merged["declare_unit"] = _text(_first(detail, ("transactionUnit",))) or _text(_first(row, ("unit",)))
        merged["origin_place"] = _text(_first(detail, ("domesticSource",)))
        merged["hscode"] = _text(_first(detail, ("productCode",)))
        merged["category"] = _text(_first(detail, ("category", ))) or _text(_first(row, ("category_name",)))
        merged["purchase_price"] = _text(_first(row, ("cg_price", "purchase_price"))) or "0"
        products[sku] = merged

    notes: List[str] = []
    if product_rows and not declared:
        notes.append(
            "未取到报关资料明细（customsDeclaration/batchDetail），中文报关名/材质/用途/HSCODE/境内货源地/报关单位"
            "在本份数据里为空；这些字段的真实来源是报关资料接口，不是产品列表接口"
        )
    return products, notes


def map_suppliers(supplier_rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """供应商列表 → 内部 suppliers（键为 supplier_id）。"""
    suppliers: Dict[str, Dict[str, Any]] = {}
    for index, row in enumerate(supplier_rows or (), start=1):
        key = _text(_first(row, ("supplier_id", "customer_supplier_id", "id"))) or str(index)
        suppliers[key] = {
            "id": key,
            "name": _text(_first(row, ("supplier_name", "name"))),
            "contact": _text(_first(row, ("contact_person", "contact"))),
            "tax_no": _text(_first(row, ("credit_code", "tax_no"))),
        }
    return suppliers


def map_batches(
    shipment_rows: Iterable[Dict[str, Any]],
    box_rows: Iterable[Dict[str, Any]] = (),
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """货件列表（+ 装箱明细）→ 内部 batches。一个货件算一个批次。

    批次号直接取官方 shipment_id（稳定、可回溯）；发货单号取 shipment_id；
    合同号取 reference_id（官方无「合同号」字段，reference_id 是货件自身的外部参考号）。
    """
    cartons_by_shipment: Dict[str, int] = {}
    for group in box_rows or ():
        inbound = _text(_first(group, ("inboundPlanId", "inbound_plan_id")))
        for shipment in group.get("shipmentList") or []:
            shipment_id = _text(_first(shipment, ("shipmentId", "shipment_id")))
            boxes = 0
            for packing in shipment.get("shipmentPackingList") or []:
                try:
                    boxes += int(packing.get("total") or 0)
                except (TypeError, ValueError):
                    continue
            if shipment_id:
                cartons_by_shipment[shipment_id] = boxes
            if inbound:
                cartons_by_shipment.setdefault("inbound:" + inbound, boxes)

    batches: List[Dict[str, Any]] = []
    for row in shipment_rows or ():
        shipment_id = _text(_first(row, ("shipment_id", "shipmentId")))
        if not shipment_id:
            continue
        lines: List[Dict[str, Any]] = []
        for item in row.get("item_list") or row.get("itemList") or []:
            shipped = _first(item, ("quantity_shipped", "quantityShipped"))
            lines.append({
                "sku": _text(_first(item, ("sku",))),
                "qty": int(shipped) if str(shipped or "").lstrip("-").isdigit() else 0,
                "cartons": "",
                "weight_kg": "",
                "supplier_id": "",
                "purchase_qty": int(shipped) if str(shipped or "").lstrip("-").isdigit() else 0,
                "shipment_qty": int(shipped) if str(shipped or "").lstrip("-").isdigit() else 0,
            })
        batches.append({
            "batch_no": shipment_id,
            "shipment_no": shipment_id,
            "contract_no": _text(_first(row, ("reference_id", "contract_no"))),
            "channel": "",
            "marketplace": _text(_first(row, ("destination_fulfillment_center_id", "ship_to_address"))),
            "shipment_name": _text(_first(row, ("shipment_name",))),
            "shipment_status": _first(row, ("shipment_status",)),
            "cartons": cartons_by_shipment.get(shipment_id, ""),
            "lines": lines,
        })
    notes = ["%s：%s" % (key, value) for key, value in BATCH_UNMAPPED.items()]
    return batches, notes


def batch_unmapped_fields() -> Dict[str, str]:
    """内部批次形状里，官方接口没有对应字段的项（给报告与文档共用）。"""
    return dict(BATCH_UNMAPPED)

