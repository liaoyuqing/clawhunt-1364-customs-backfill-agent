"""模块一知识库沉淀：同名报关名第二次出现不再新增条目，编码冲突只记录不覆盖。"""

from __future__ import annotations

from core.knowledge import (
    CONFIRM_INTERNAL,
    CONFIRM_SUPPLIER,
    STATUS_CONFLICT,
    STATUS_NEW,
    STATUS_REUSED,
    STATUS_SKIPPED,
    DeclareNameBook,
)


def test_name_book_first_time_is_new_second_time_is_reused(rules):
    """第二次遇到同名品名不再新增条目。"""
    book = DeclareNameBook(rules.brand_words)
    assert book.observe("桌面收纳盒", "SKU-1001") == STATUS_NEW
    size_after_first = len(book.index)
    assert size_after_first == 1
    assert len(book.pending) == 1
    assert book.observe("桌面收纳盒", "SKU-1001") == STATUS_REUSED
    assert len(book.index) == size_after_first, "第二次不得新增条目"
    assert len(book.pending) == 1, "第二次不得新增待确认"
    assert book.status_counts()[STATUS_NEW] == 1
    assert book.status_counts()[STATUS_REUSED] == 1


def test_name_book_normalises_brand_words_and_spacing(rules):
    """品牌词与空格被清洗后视为同名。"""
    book = DeclareNameBook(rules.brand_words)
    book.observe("桌面收纳盒", "SKU-1001")
    assert book.observe(" 桌面收纳盒 ", "SKU-1001") == STATUS_REUSED
    assert len(book.index) == 1


def test_name_book_conflict_keeps_first_mapping_and_records_it(rules):
    book = DeclareNameBook(rules.brand_words)
    assert book.observe("桌面收纳盒", "SKU-1001") == STATUS_NEW
    assert book.observe("桌面收纳盒", "SKU-9999") == STATUS_CONFLICT
    assert book.resolve("桌面收纳盒") == "SKU-1001"
    assert book.index.conflicts == [("桌面收纳盒", "SKU-1001", "SKU-9999")]
    payload = book.to_dict()
    assert payload["conflicts"] == 1
    assert payload["conflict_rows"][0]["left"] == "SKU-1001"


def test_name_book_confirm_requires_both_checks(rules):
    book = DeclareNameBook(rules.brand_words)
    book.observe("桌面收纳盒", "SKU-1001")
    assert book.confirm("桌面收纳盒", supplier_ok=True, internal_ok=False, operator="ops") is False
    assert len(book.pending) == 1
    assert book.confirm("桌面收纳盒", supplier_ok=False, internal_ok=True, operator="ops") is True
    assert book.pending == []
    assert book.confirmed["桌面收纳盒"]["operator"] == "ops"
    assert book.confirm("不存在品名", True, True) is False


def test_name_book_skips_empty_values(rules):
    book = DeclareNameBook(rules.brand_words)
    assert book.observe("", "SKU-1") == STATUS_SKIPPED
    assert book.observe("品名", "") == STATUS_SKIPPED
    assert len(book.index) == 0


def test_name_book_is_wired_into_cli_pipeline(rules, products):
    """生产链路真的建了这个知识库：50 批次 11 个产品，去重后条目数小于等于产品数。"""
    from core import pipeline

    book = pipeline.build_name_book(rules, products)
    assert len(book.index) > 0
    assert len(book.index) <= len(products)
    counts = book.status_counts()
    assert counts[STATUS_NEW] >= 1
    pending_needs = {item["need"] for item in book.pending}
    assert pending_needs == {"%s+%s" % (CONFIRM_SUPPLIER, CONFIRM_INTERNAL)}


def test_name_book_reuses_duplicate_declare_names_across_skus(rules, products):
    """SKU-1003 与 SKU-1005 报关名同为「文具套装」，第二次必须复用而不是新增。"""
    from core import pipeline

    book = pipeline.build_name_book(rules, products)
    usable = [products[sku] for sku in sorted(products) if str(products[sku].get("hscode", "")).strip()]
    names = [item["declare_name"] for item in usable]
    skipped = len(products) - len(usable)
    duplicates = len(names) - len(set(names))
    assert duplicates >= 1, "夹具里必须有同名不同 SKU"
    assert len(book.index) == len(set(names))
    assert book.status_counts()[STATUS_REUSED] == duplicates
    assert book.status_counts()[STATUS_SKIPPED] == skipped
    assert book.status_counts()[STATUS_CONFLICT] == 0, "同名同海关编码不是冲突"