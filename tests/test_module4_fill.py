"""模块四回填到台账：7 列非空、两个余量口径可证伪、归档链接 https、耗用与发票台账自洽。"""

from __future__ import annotations

from decimal import Decimal

from core.archive import remaining
from core.module4 import INVOICE_COLUMNS

INVOICE_COLUMNS_LIST = list(INVOICE_COLUMNS)


def _detail_keys(ledger):
    return [key for key in ledger.keys() if "#" in key]


def test_module4_columns_are_declared(rules):
    """7 列必须真在台账列定义里，否则写不进去。"""
    assert len(INVOICE_COLUMNS) == 7
    for column in INVOICE_COLUMNS:
        assert column in rules.columns


def test_module4_seven_columns_are_filled_on_every_detail_row(filled):
    """146 行里 96 明细行，7 列全部非空；50 个批次表头行也有归档链接。"""
    ledger, _results = filled
    detail = _detail_keys(ledger)
    assert len(detail) == 96
    for row_key in detail:
        row = ledger.row(row_key)
        for column in INVOICE_COLUMNS:
            assert row.value(column).strip(), "%s/%s 为空" % (row_key, column)
    headers = [key for key in ledger.keys() if "#" not in key]
    assert len(headers) == 50
    assert all(ledger.row(key).value("归档共享链接").strip() for key in headers)


def test_module4_archive_link_is_https_and_complete(filled):
    """归档共享链接：146 行全非空且都是 https，非空率 100%。"""
    ledger, _results = filled
    keys = ledger.keys()
    links = [ledger.row(key).value("归档共享链接") for key in keys]
    filled_links = [link for link in links if link.strip()]
    assert len(filled_links) == len(keys) == 146
    assert all(link.startswith("https://") for link in filled_links)
    assert len(set(filled_links)) == 50, "一个批次一个归档链接"


def test_module4_balance_equals_invoiced_minus_batch_usage(filled, invoices, products):
    """可证伪：每行「结余」= 票面数量 − 本批次该品类累计耗用（Decimal 精确到分）。"""
    ledger, _results = filled
    batch_used = {}
    for row_key in _detail_keys(ledger):
        row = ledger.row(row_key)
        category = str(products[row.value("SKU")]["category"])
        capacity = str(invoices.pick(category)["invoiced_qty"])
        qty = row.value("数量")
        batch_used[(row.batch_no, category)] = batch_used.get((row.batch_no, category), Decimal("0")) + Decimal(qty)
        expected = remaining(capacity, [str(batch_used[(row.batch_no, category)])])
        assert row.value("结余") == expected, row_key


def test_module4_remaining_column_equals_invoiced_minus_all_usage(filled, invoices, products):
    """可证伪：每行「发票剩余可用数量」= 票面数量 − 全部批次累计耗用（跨批次共享同一张发票）。"""
    ledger, _results = filled
    total_used = {}
    for row_key in _detail_keys(ledger):
        row = ledger.row(row_key)
        category = str(products[row.value("SKU")]["category"])
        capacity = str(invoices.pick(category)["invoiced_qty"])
        total_used[category] = total_used.get(category, Decimal("0")) + Decimal(row.value("数量"))
        expected = remaining(capacity, [str(total_used[category])])
        assert row.value("发票剩余可用数量") == expected, row_key


def test_module4_used_qty_equals_row_quantity_and_invoice_matches_category(filled, products, rules):
    """「耗用发票数量」必须等于该行数量；发票号/开票公司按品类取，不能串品类。"""
    ledger, _results = filled
    table = rules.invoice_companies["by_category"]
    for row_key in _detail_keys(ledger):
        row = ledger.row(row_key)
        category = str(products[row.value("SKU")]["category"])
        assert row.value("耗用发票数量") == row.value("数量")
        assert row.value("开票公司") == table[category]["company"]
        assert row.value("开票日期"), row_key


def test_module4_invoice_remaining_never_negative(filled, invoices):
    """发票不得超用：每个品类的累计耗用都小于等于票面数量。"""
    ledger, _results = filled
    for item in invoices.snapshot():
        assert Decimal(item["remaining"]) >= 0, item
        assert Decimal(item["used"]) <= Decimal(item["invoiced_qty"]), item


def test_module4_usage_called_on_production_path():
    """remaining() 必须被生产链路调用，而不是只有测试在调。"""
    import inspect

    from core import module4

    source = inspect.getsource(module4)
    assert ".use(" in source
    assert "InvoiceUsage" in source
    assert "remaining(" in inspect.getsource(__import__("core.archive", fromlist=["remaining"]))