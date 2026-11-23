"""适配层：领星分页/限流退避、钉钉列序校验、视觉低置信转人工。"""

from __future__ import annotations

import pytest

from core.declare import BatchContext, BatchFiller
from core.dingtalk import ColumnMismatch, DingTalkSheet
from core.ledger import Ledger
from core.lingxing import FixtureTransport, LingxingClient, LingxingError, RateLimited, product_declare_fields


def test_lingxing_pagination_reads_all_pages_and_no_duplicate():
    pages = {"products": [[{"id": i} for i in range(10)], [{"id": i} for i in range(10, 15)]]}
    client = LingxingClient(FixtureTransport(pages), page_size=10)
    items = client.fetch_all("products")
    assert len(items) == 15
    assert [item["id"] for item in items] == list(range(15))
    assert client.calls == ["products@0", "products@10"]


def test_lingxing_retries_on_429_with_retry_after():
    waits = []
    pages = {"products": [[{"id": 1}]]}
    transport = FixtureTransport(pages, fail_plan={"products@0": 2})
    client = LingxingClient(transport, page_size=10, sleep=waits.append)
    items = client.fetch_all("products")
    assert len(items) == 1
    assert waits == [2, 2]
    assert len(client.retries) == 2


def test_lingxing_gives_up_after_max_retries():
    transport = FixtureTransport({"products": [[{"id": 1}]]}, fail_plan={"products@0": 99})
    client = LingxingClient(transport, max_retries=2, sleep=lambda s: None)
    with pytest.raises(RateLimited):
        client.fetch_all("products")


def test_lingxing_nonzero_code_raises():
    def transport(resource, offset, size):
        return {"code": 20001, "msg": "no permission", "data": None}

    client = LingxingClient(transport)
    with pytest.raises(LingxingError) as exc:
        client.fetch_all("products")
    assert "20001" in str(exc.value)


def test_product_mapping_keeps_seven_columns():
    fields = product_declare_fields({
        "sku": "SKU-1001", "declare_name": "桌面收纳盒", "model": "M-1001", "material": "ABS 塑料",
        "usage": "家居收纳", "declare_unit": "个", "origin_place": "深圳", "hscode": "3924900000",
    })
    assert set(fields) == {"产品中文报关名", "型号", "材质", "用途", "报关单位", "境内货源地", "HSCODE", "SKU"}
    assert fields["HSCODE"] == "3924900000"


def test_dingtalk_rejects_header_drift(rules):
    DingTalkSheet(rules.columns)
    drifted = list(rules.columns)
    drifted[0] = "批次编号"
    with pytest.raises(ColumnMismatch):
        DingTalkSheet(rules.columns, header=drifted)


def test_dingtalk_write_unknown_column_rejected(rules):
    sheet = DingTalkSheet(rules.columns)
    with pytest.raises(ColumnMismatch):
        sheet.write_cells("batch:B1", {"不存在的列": "x"}, None)


def test_vision_low_confidence_goes_to_manual_queue(rules, batches, products, suppliers):
    batch = {
        "batch_no": "B-VIS-01",
        "shipment_no": "FBA-SF-55555",
        "contract_no": "HT-2026-5555",
        "lines": [{"sku": "SKU-1001", "qty": 2, "cartons": 1, "weight_kg": "0.62", "supplier_id": "S01",
                   "purchase_qty": 2, "shipment_qty": 2}],
    }
    vision = {
        "batch:B-VIS-01": {
            "screenshot_id": "shot-0001",
            "fields": [
                {"name": "HSCODE", "text": "3924900000", "bbox": [10, 20, 120, 40], "confidence": 0.97},
                {"name": "报关单价", "text": "7.68", "bbox": [10, 60, 120, 80], "confidence": 0.61},
            ],
            "note": "供应商回传件",
        }
    }
    ledger = Ledger(rules.columns)
    result = BatchFiller(rules).fill(
        ledger,
        BatchContext(batch=batch, products=products, suppliers=suppliers, vision=vision),
    )
    codes = [issue.code for issue in result.issues]
    assert "vision_low_confidence" in codes
    low = [issue for issue in result.issues if issue.code == "vision_low_confidence"]
    assert low[0].column == "报关单价"
    row = ledger.row("B-VIS-01")
    assert row.value("HSCODE") == "3924900000"
    assert row.get("HSCODE").source.kind == "vision"
    assert row.get("HSCODE").source.ref == "shot-0001"