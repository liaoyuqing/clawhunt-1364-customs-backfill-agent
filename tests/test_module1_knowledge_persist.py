"""买家退返第 4 条：模块一品名的两步确认与知识库持久化。

状态机：pending_supplier -> pending_internal -> confirmed / rejected。
未到 confirmed 不得写成台账确认编码；进程重启后知识库仍能读到该品名。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from core.knowledge import (
    STATE_CONFIRMED,
    STATE_PENDING_INTERNAL,
    STATE_PENDING_SUPPLIER,
    STATE_REJECTED,
    DeclareNameBook,
    default_db_path,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _book(tmp_path, name="knowledge.json"):
    return DeclareNameBook({}, path=os.path.join(str(tmp_path), name))


def test_new_name_starts_in_pending_supplier(tmp_path):
    book = _book(tmp_path)
    book.observe("桌面收纳盒", "3924900000")
    assert book.state_of("桌面收纳盒") == STATE_PENDING_SUPPLIER
    assert book.pending[0]["state"] == STATE_PENDING_SUPPLIER
    assert book.confirmed_codes() == {}


def test_supplier_confirmation_still_needs_internal_review(tmp_path):
    book = _book(tmp_path)
    book.observe("桌面收纳盒", "3924900000")
    book.confirm_supplier("桌面收纳盒", operator="供应商张", note="同款确认")
    assert book.state_of("桌面收纳盒") == STATE_PENDING_INTERNAL
    assert book.confirmed_codes() == {}, "供应商确认后仍未沉淀完成"
    assert book.confirm_internal("桌面收纳盒", operator="内部李") is True
    assert book.state_of("桌面收纳盒") == STATE_CONFIRMED
    assert book.confirmed_codes() == {"桌面收纳盒": "3924900000"}


def test_internal_confirmation_is_ignored_without_supplier_step(tmp_path):
    book = _book(tmp_path)
    book.observe("桌面收纳盒", "3924900000")
    assert book.confirm_internal("桌面收纳盒", operator="内部李") is False
    assert book.state_of("桌面收纳盒") == STATE_PENDING_SUPPLIER


def test_rejected_name_is_never_confirmed(tmp_path):
    book = _book(tmp_path)
    book.observe("桌面收纳盒", "3924900000")
    book.confirm_supplier("桌面收纳盒", operator="供应商张")
    assert book.reject("桌面收纳盒", operator="内部李", note="编码不对") is True
    assert book.state_of("桌面收纳盒") == STATE_REJECTED
    assert book.confirmed_codes() == {}


def test_knowledge_survives_a_new_process(tmp_path):
    """新建进程后知识库仍能读到该品名与其确认状态。"""
    path = os.path.join(str(tmp_path), "knowledge.json")
    book = DeclareNameBook({}, path=path)
    book.observe("桌面收纳盒", "3924900000")
    book.confirm_supplier("桌面收纳盒", operator="供应商张")
    book.confirm_internal("桌面收纳盒", operator="内部李")

    """子进程输出必须与控制台代码页无关：ensure_ascii=True 让 stdout 全是 ASCII，
    父进程 json.loads 之后中文自动还原；再显式把 stdout 设成 UTF-8 作双保险。"""
    probe = os.path.join(str(tmp_path), "probe.py")
    probe_source = "\n".join([
        "import json, sys",
        "try:",
        "    sys.stdout.reconfigure(encoding='utf-8')",
        "except (AttributeError, ValueError):",
        "    pass",
        "sys.path.insert(0, %r)" % ROOT,
        "from core.knowledge import DeclareNameBook",
        "book = DeclareNameBook({}, path=%r)" % path,
        "print(json.dumps({'state': book.state_of('桌面收纳盒'), "
        " 'confirmed': book.confirmed_codes(), "
        " 'entries': len(book.entries), "
        " 'errors': book.load_errors}, ensure_ascii=True))",
    ])
    with open(probe, "w", encoding="utf-8") as handle:
        handle.write(probe_source)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, probe],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["state"] == STATE_CONFIRMED
    assert payload["confirmed"] == {"桌面收纳盒": "3924900000"}
    assert payload["entries"] == 1
    assert payload["errors"] == []


def test_default_db_path_is_outside_the_repository():
    """默认库文件落在用户目录，不写进仓库。"""
    path = default_db_path()
    assert path.endswith(os.path.join(".clawhunt-1364", "knowledge.json"))
    assert not os.path.abspath(path).startswith(os.path.abspath(ROOT) + os.sep)


def _product():
    return {
        "sku": "SKU-1001",
        "declare_name": "桌面收纳盒",
        "model": "M-1001",
        "material": "塑料",
        "usage": "收纳",
        "declare_unit": "个",
        "origin_place": "深圳",
        "hscode": "3924900000",
        "category": "家居",
        "purchase_price": "12.80",
    }


def _batch(batch_no):
    return {
        "batch_no": batch_no,
        "shipment_no": "SF-" + batch_no,
        "contract_no": "HT-" + batch_no,
        "lines": [
            {"sku": "SKU-1001", "qty": 2, "cartons": 1, "weight_kg": "0.5",
             "supplier_id": "S01", "purchase_qty": 2, "shipment_qty": 2},
        ],
    }


def test_pipeline_uses_persistent_book_and_gates_unconfirmed_names(tmp_path):
    """知识库接进流水线：未确认的品名不写确认编码，并报一条待办。"""
    from core import pipeline
    from core.config import Rules

    rules = Rules()
    path = os.path.join(str(tmp_path), "knowledge.json")
    products = {"SKU-1001": _product()}
    suppliers = {"S01": {"id": "S01", "name": "深圳宝安收纳制品厂"}}
    batches = [_batch("KB-0001")]
    invoices = pipeline.load_invoices(os.path.join(ROOT, "fixtures", "invoices", "ledger.json"))

    book = pipeline.build_name_book(rules, products, path=path)
    result = pipeline.run_pipeline(
        rules, batches, products, suppliers, invoices, name_book=book, gate_unconfirmed_names=True
    )
    codes = [issue.code for issue in result["issues"]]
    assert "name_not_confirmed" in codes
    row = result["ledger"].row("KB-0001#0")
    assert row.value("HSCODE") == "", "未确认的品名不得写确认编码"

    """两步确认走完后，同一个品名的确认编码才写进台账。"""
    book.confirm_supplier("桌面收纳盒", operator="供应商张")
    book.confirm_internal("桌面收纳盒", operator="内部李")
    result2 = pipeline.run_pipeline(
        rules, batches, products, suppliers, invoices, name_book=book, gate_unconfirmed_names=True
    )
    row2 = result2["ledger"].row("KB-0001#0")
    assert row2.value("HSCODE") == "3924900000"
    assert row2.get("HSCODE").source.kind == "manual"


def test_confirmed_code_conflicts_with_source_are_reported(tmp_path):
    """知识库编码与源系统不一致时报一条冲突，但仍以确认编码为准。"""
    from core import pipeline
    from core.config import Rules

    rules = Rules()
    path = os.path.join(str(tmp_path), "knowledge.json")
    products = {"SKU-1001": _product()}
    suppliers = {"S01": {"id": "S01", "name": "深圳宝安收纳制品厂"}}
    batches = [_batch("KB-0002")]
    invoices = pipeline.load_invoices(os.path.join(ROOT, "fixtures", "invoices", "ledger.json"))
    book = pipeline.build_name_book(rules, products, path=path)
    book.confirm_supplier("桌面收纳盒", operator="供应商张")
    book.confirm_internal("桌面收纳盒", operator="内部李")
    book.entries["桌面收纳盒"]["code"] = "3924909999"
    book.confirmed["桌面收纳盒"]["code"] = "3924909999"
    result = pipeline.run_pipeline(
        rules, batches, products, suppliers, invoices, name_book=book, gate_unconfirmed_names=True
    )
    codes = [issue.code for issue in result["issues"]]
    assert "confirmed_code_conflict" in codes
    assert result["ledger"].row("KB-0002#0").value("HSCODE") == "3924909999"