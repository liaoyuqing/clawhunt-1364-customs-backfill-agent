"""模块一：合同品名三处一致、要件检查、知识库品名归并。"""

from __future__ import annotations

import pytest

from core.contract import (
    ContractLine,
    NameMismatch,
    assert_name_consistency,
    build_contract,
    requirement_issues,
)
from core.naming import NameIndex, merge_key, strip_brand_words


def _line(**overrides):
    payload = dict(
        sku="SKU-1001",
        product_name="桌面收纳盒",
        invoice_name="桌面收纳盒",
        declare_name="桌面收纳盒",
        spec="型号 M-1001",
        unit="个",
        qty="120",
        unit_price="12.80",
    )
    payload.update(overrides)
    return ContractLine(**payload)


def test_module1_names_must_match_all_three_places():
    assert_name_consistency([_line()])
    with pytest.raises(NameMismatch):
        assert_name_consistency([_line(invoice_name="桌面收纳盒（大号）")])
    with pytest.raises(NameMismatch):
        assert_name_consistency([_line(declare_name="收纳盒")])


def test_module1_contract_totals_and_text():
    contract = build_contract("HT-2026-0001", "深圳宝安收纳制品厂", "客户公司", [_line(), _line(sku="SKU-1002", qty="30", unit_price="6.40")])
    assert contract.total_amount() == "1728.00"
    text = contract.as_text()
    assert "HT-2026-0001" in text and "1728.00" in text


def test_module1_requirement_issues_list_everything_not_short_circuit():
    contract = build_contract("HT-2026-0002", "供方", "需方", [_line()])
    issues = requirement_issues(contract, attachments=[])
    codes = sorted(issue.code for issue in issues)
    assert codes == ["missing_requirement", "missing_requirement", "missing_requirement", "stamp_missing"]
    columns = sorted(issue.column for issue in issues if issue.code == "missing_requirement")
    assert columns == sorted(["供应商营业执照", "报价单", "盖章回传件"])


def test_module1_requirement_ok_when_everything_present():
    contract = build_contract("HT-2026-0003", "供方", "需方", [_line()])
    contract.stamp_received = True
    issues = requirement_issues(contract, attachments=["供应商营业执照", "报价单", "盖章回传件"])
    assert issues == []


def test_module1_name_merge_strips_brand_words(rules):
    cfg = rules.brand_words
    assert strip_brand_words("桌面收纳盒 Pro 旗舰版", cfg) == "桌面收纳盒"
    assert strip_brand_words("桌面收纳盒官方正品", cfg) == "桌面收纳盒"
    assert merge_key("桌面收纳盒 PRO", cfg) == merge_key("桌面收纳盒", cfg)
    assert merge_key("桌面收纳盒", cfg) != merge_key("桌面笔筒", cfg)


def test_module1_index_collision_recorded_not_silently_overwritten(rules):
    index = NameIndex(rules.brand_words)
    assert index.add("桌面收纳盒 Pro", "SKU-1001") is True
    assert index.add("桌面收纳盒官方正品", "SKU-1001") is True
    assert index.add("桌面收纳盒", "SKU-9999") is False
    assert index.find("桌面收纳盒 Plus") == "SKU-1001"
    assert len(index) == 1
    assert index.conflicts and index.conflicts[0][1] == "SKU-1001"