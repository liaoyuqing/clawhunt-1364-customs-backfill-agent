"""模块二补充：多批次合并报关判定、部分报关按箱段、配置版本守卫。"""

from __future__ import annotations

import pytest

from core.config import ConfigError, Rules
from core.declare import BatchContext, BatchFiller, merge_batches, partial_declare
from core.ledger import Ledger
from core.models import DuplicateWrite, UnknownColumn


def _fill(rules, products, suppliers, batch):
    ledger = Ledger(rules.columns)
    BatchFiller(rules).fill(ledger, BatchContext(batch=batch, products=products, suppliers=suppliers))
    return ledger


def test_merge_batches_groups_only_identical_keys(rules, products, suppliers):
    batch_a = {"batch_no": "M-A", "shipment_no": "S-A", "contract_no": "C-A",
               "lines": [{"sku": "SKU-1001", "qty": 2, "cartons": 10, "weight_kg": "0.62", "supplier_id": "S01",
                          "purchase_qty": 2, "shipment_qty": 2}]}
    batch_b = {"batch_no": "M-B", "shipment_no": "S-B", "contract_no": "C-B",
               "lines": [{"sku": "SKU-1001", "qty": 5, "cartons": 20, "weight_kg": "1.55", "supplier_id": "S01",
                          "purchase_qty": 5, "shipment_qty": 5}]}
    batch_c = {"batch_no": "M-C", "shipment_no": "S-C", "contract_no": "C-C",
               "lines": [{"sku": "SKU-1002", "qty": 3, "cartons": 8, "weight_kg": "0.93", "supplier_id": "S02",
                          "purchase_qty": 3, "shipment_qty": 3}]}
    ledgers = [_fill(rules, products, suppliers, b) for b in (batch_a, batch_b, batch_c)]
    groups = merge_batches(ledgers)
    mergable = [g for g in groups if g["mergable"]]
    assert len(mergable) == 1
    assert sorted(mergable[0]["rows"]) == ["M-A#0", "M-B#0"]
    assert groups[0]["rows"] == ["M-A#0", "M-B#0"]
    header = [g for g in groups if g.get("skipped")]
    assert header and sorted(header[0]["rows"]) == ["M-A", "M-B", "M-C"]
    assert len(mergable[0]["key"]) == 5


def test_partial_declare_selects_carton_range(rules, products, suppliers):
    batch = {"batch_no": "P-A", "shipment_no": "S-P", "contract_no": "C-P",
             "lines": [
                 {"sku": "SKU-1001", "qty": 2, "cartons": 5, "weight_kg": "0.62", "supplier_id": "S01",
                  "purchase_qty": 2, "shipment_qty": 2},
                 {"sku": "SKU-1002", "qty": 3, "cartons": 12, "weight_kg": "0.93", "supplier_id": "S02",
                  "purchase_qty": 3, "shipment_qty": 3},
             ]}
    ledger = _fill(rules, products, suppliers, batch)
    result = partial_declare(ledger.rows(), 1, 6)
    assert result["rows"] == ["P-A#0"]
    assert result["cartons"] == 5
    assert result["carton_from"] == 1 and result["carton_to"] == 6
    with pytest.raises(ValueError):
        partial_declare(ledger.rows(), 9, 3)


def test_ledger_rejects_duplicate_write_and_unknown_column(rules):
    from core.models import SourceRef

    ledger = Ledger(rules.columns)
    src = SourceRef(kind="api", ref="lingxing/products", at="2026-11-23T00:00:00+08:00")
    ledger.write("R1", "批次号", "B1", src)
    with pytest.raises(DuplicateWrite):
        ledger.write("R1", "批次号", "B2", src)
    with pytest.raises(UnknownColumn):
        ledger.write("R1", "不存在的列", "x", src)


def test_source_kind_whitelist_enforced():
    from core.models import SourceRef

    SourceRef(kind="vision", ref="shot-1", at="t")
    with pytest.raises(ValueError):
        SourceRef(kind="guess", ref="x", at="t")
    with pytest.raises(ValueError):
        SourceRef(kind="api", ref="", at="t")


def test_rules_reject_future_effective_date(rules):
    with pytest.raises(ConfigError) as exc:
        Rules(root=rules.root, as_of="2026-10-01")
    assert "effective from" in str(exc.value)


def test_rules_reject_missing_file(tmp_path):
    (tmp_path / "bundle.yaml").write_text("version: 1\neffective_from: 2026-01-01\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        Rules(root=str(tmp_path))
    assert "rule file missing" in str(exc.value)


def test_rules_reject_missing_version_key(tmp_path, rules):
    import shutil

    for name in ("bundle", "unit", "brand_words", "declare_price", "required_fields", "invoice_companies", "ledger_columns"):
        shutil.copyfile(f"{rules.root}/{name}.yaml", tmp_path / f"{name}.yaml")
    target = tmp_path / "unit.yaml"
    target.write_text("effective_from: 2026-01-01\nfactors:\n  套->个: 12\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        Rules(root=str(tmp_path))
    assert "missing version" in str(exc.value)