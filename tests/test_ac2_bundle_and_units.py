"""AC2：组合拆分、赠品与替代料标记、按套/按个换算，全部读配置。"""

from __future__ import annotations

import copy

import pytest

from core import bundle as bundle_mod
from core.config import Rules
from core.declare import BatchContext, BatchFiller
from core.ledger import Ledger
from core.units import convert, factor, quantity_text


def _fill_one(rules, batch, products, suppliers):
    ledger = Ledger(rules.columns)
    result = BatchFiller(rules).fill(ledger, BatchContext(batch=batch, products=products, suppliers=suppliers))
    return ledger, result


@pytest.mark.parametrize(
    "sku,qty,expected",
    [
        ("BND-2S", 8, [("SKU-1001", "16"), ("SKU-1002", "8")]),
        ("BND-3S", 4, [("SKU-1001", "4"), ("SKU-1002", "4"), ("SKU-1003", "4")]),
        ("BND-GIFT", 3, [("SKU-1002", "3"), ("SKU-1004", "3")]),
        ("BND-ALT", 5, [("SKU-1003", "5"), ("SKU-1005", "5")]),
    ],
)
def test_ac2_split_matches_configured_children(rules, sku, qty, expected):
    lines = bundle_mod.split(sku, qty, rules.bundles)
    assert [(line.sku, line.qty) for line in lines] == expected


def test_ac2_gift_and_alt_markers_written_to_model_column(rules, batches, products, suppliers):
    gift_batch = {
        "batch_no": "B-GIFT-01",
        "shipment_no": "FBA-SF-77777",
        "contract_no": "HT-2026-7777",
        "lines": [{"sku": "BND-GIFT", "qty": 2, "cartons": 4, "weight_kg": "1.10", "supplier_id": "S03",
                   "purchase_qty": 2, "shipment_qty": 2}],
    }
    ledger, _result = _fill_one(rules, gift_batch, products, suppliers)
    models = [ledger.row(key).value("型号") for key in ledger.keys() if key.startswith("B-GIFT-01#")]
    assert any(model.endswith("|赠品") for model in models)
    assert sum(1 for m in models if "赠品" in m) == 1

    alt_batch = dict(gift_batch)
    alt_batch["batch_no"] = "B-ALT-01"
    alt_batch["contract_no"] = "HT-2026-8888"
    alt_batch["lines"] = [{"sku": "BND-ALT", "qty": 1, "cartons": 2, "weight_kg": "0.40", "supplier_id": "S02",
                           "purchase_qty": 1, "shipment_qty": 1}]
    ledger2, _ = _fill_one(rules, alt_batch, products, suppliers)
    models2 = [ledger2.row(key).value("型号") for key in ledger2.keys() if key.startswith("B-ALT-01#")]
    assert models2 == ["M-1003", "M-1005|替代:SKU-1003"]


def test_ac2_units_come_from_yaml(tmp_path, batches, products, suppliers):
    """换一份 unit.yaml（12 -> 10），换算结果必须跟着变，证明没有硬编码。"""
    import shutil

    base_rules = Rules()
    source_root = base_rules.root
    for name in ("bundle", "unit", "brand_words", "declare_price", "required_fields", "invoice_companies", "ledger_columns"):
        shutil.copyfile(
            f"{source_root}/{name}.yaml",
            tmp_path / f"{name}.yaml",
        )
    unit_path = tmp_path / "unit.yaml"
    text = unit_path.read_text(encoding="utf-8").replace("套->个: 12", "套->个: 10")
    unit_path.write_text(text, encoding="utf-8")
    patched = Rules(root=str(tmp_path), as_of="2026-12-01")
    assert patched.unit_factors["套->个"] == 10
    assert convert(3, "套", "个", patched.unit_factors) == 30
    assert factor("套", "个", base_rules.unit_factors) == 12


def test_ac2_unit_conversions_are_decimal_exact(rules):
    cfg = rules.unit_factors
    assert str(convert(1, "箱", "个", cfg)) == "24"
    assert str(convert(2, "套", "箱", cfg)) == "1.0"
    assert quantity_text(convert(2, "套", "箱", cfg)) == "1"
    with pytest.raises(KeyError):
        factor("个", "吨", cfg)


def test_ac2_non_bundle_single_line(rules, batches, products, suppliers):
    batch = {
        "batch_no": "B-PLAIN-01",
        "shipment_no": "FBA-SF-66666",
        "contract_no": "HT-2026-6666",
        "lines": [{"sku": "SKU-1001", "qty": 7, "cartons": 3, "weight_kg": "2.17", "supplier_id": "S01",
                   "purchase_qty": 7, "shipment_qty": 7}],
    }
    ledger, result = _fill_one(rules, batch, products, suppliers)
    assert len(result.row_keys) == 2
    row = ledger.row("B-PLAIN-01#0")
    assert row.value("SKU") == "SKU-1001"
    assert row.value("数量") == "7"
    assert row.value("报关单价") == "7.68"