"""AC2 补充：单位换算接进回填链路，原始单位与系数留痕，换算反例必须报出来。"""

from __future__ import annotations

import pytest

from core import pipeline
from core.config import Rules
from core.declare import BatchContext, BatchFiller
from core.ledger import Ledger
from core.units import base_unit


def test_ac2_sku_1003_is_converted_to_base_unit(rules, batches, products, suppliers):
    """SKU-1003 报关单位=套、数量=8，写入台账必须是 个/96，不是 套/8。"""
    ledger = Ledger(rules.columns)
    filler = BatchFiller(rules)
    for batch in batches:
        result = filler.fill(ledger, BatchContext(batch=batch, products=products, suppliers=suppliers))
        for line, row_key in zip(batch["lines"], result.row_keys[1:]):
            if str(line["sku"]) != "SKU-1003":
                continue
            row = ledger.row(row_key)
            raw = int(str(line["qty"]))
            assert row.value("报关单位") == "个"
            assert row.value("数量") == str(raw * 12)
            assert row.value("报关单价") != ""


def test_ac2_conversions_are_recorded_with_original_unit(rules, batches, products, suppliers):
    """换算留痕：每条记录都带原始单位/原始数量/系数/折算后数量，台账列序不变。"""
    result = pipeline.run_pipeline(
        rules, batches, products, suppliers,
        pipeline.load_invoices(__import__("os").path.join("fixtures", "invoices", "ledger.json")),
    )
    conversions = result["conversions"]
    assert conversions, "50 批次里必须有套转个的行"
    assert all(item["from_unit"] == "套" and item["to_unit"] == "个" for item in conversions)
    assert all(str(item["scale"]) == "12" for item in conversions)
    for item in conversions:
        assert int(item["to_qty"]) == int(item["from_qty"]) * 12


def test_ac2_base_unit_comes_from_yaml(rules):
    assert base_unit(rules.unit_doc) == "个"
    doc = dict(rules.unit_doc)
    doc["base_unit"] = "箱"
    assert base_unit(doc) == "箱"


def test_ac2_unit_not_configured_issue_is_raised():
    """反例：报关单位不在换算表里（托盘），必须报 unit_not_configured 并按原单位写入。"""
    rules = Rules()
    batches, products, suppliers = pipeline.load_fixtures("fixtures/reconciliation")
    invoices = pipeline.load_invoices("fixtures/invoices/ledger.json")
    result = pipeline.run_pipeline(rules, batches, products, suppliers, invoices)
    codes = [issue.code for issue in result["issues"]]
    assert "unit_not_configured" in codes
    ledger = result["ledger"]
    hit = [key for key in ledger.keys() if "#" in key and ledger.row(key).value("SKU") == "RC-2"]
    assert hit
    assert ledger.row(hit[0]).value("报关单位") == "托盘"


def test_ac2_declare_price_scales_with_unit_factor(rules, products):
    """单价同除系数：3.20 × 0.58 ÷ 1 = 1.856；套转个时除 12。"""
    from core.price import declare_price

    gift = products["SKU-1004"]
    assert declare_price(gift["purchase_price"], "礼品", rules.price_rules) == "1.856"
    scaled = declare_price("9.90", "文具", rules.price_rules, 12)
    assert float(scaled) == pytest.approx(0.53625, abs=1e-9)