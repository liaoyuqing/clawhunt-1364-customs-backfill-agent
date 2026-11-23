"""离线主流水线：模块二回填 + 模块四发票/归档回填，串成一条可复现的链路。"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

from .config import Rules
from .declare import BatchContext, BatchFiller, FillResult, contract_no_issues
from .knowledge import DeclareNameBook
from .ledger import Ledger
from .models import Issue
from .module4 import InvoiceLedger, Module4Filler, Module4Result

AT = "2026-11-23T00:00:00+08:00"


def load_json(path: str) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def load_fixtures(fixtures_dir: str) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """批次 / 产品 / 供应商三份夹具，全部是合成数据。"""
    batches = load_json(os.path.join(fixtures_dir, "batches.json"))
    products = {str(item["sku"]): item for item in load_json(os.path.join(fixtures_dir, "products.json"))}
    suppliers = {str(item["id"]): item for item in load_json(os.path.join(fixtures_dir, "suppliers.json"))}
    return batches, products, suppliers


def load_invoices(path: str) -> InvoiceLedger:
    return InvoiceLedger(records=load_json(path))


def build_name_book(rules: Rules, products: Dict[str, Dict[str, Any]]) -> DeclareNameBook:
    """模块一知识库沉淀：报关名 -> 标准编码（海关编码）。

    同一个报关名第二次出现时直接复用已登记的海关编码，不再新增条目；只有同名却
    指向不同海关编码时才记为冲突（保留首个，不覆盖）。空报关名或空海关编码跳过。
    """
    book = DeclareNameBook(rules.brand_words)
    for sku in sorted(products):
        product = products[sku]
        book.observe(str(product.get("declare_name", "")), str(product.get("hscode", "")))
    return book


def run_pipeline(
    rules: Rules,
    batches: List[Dict[str, Any]],
    products: Dict[str, Dict[str, Any]],
    suppliers: Dict[str, Dict[str, Any]],
    invoices: InvoiceLedger,
    archive_root: Optional[str] = None,
    at: str = AT,
) -> Dict[str, Any]:
    """跑完 50 批次：模块二写 16 列，模块四补后 7 列，最后做批次间勾稽。"""
    ledger = Ledger(rules.columns)
    filler = BatchFiller(rules, at=at)
    m4 = Module4Filler(rules, invoices, archive_root=archive_root, at=at)
    fills: List[FillResult] = []
    module4: List[Module4Result] = []
    for batch in batches:
        result = filler.fill(ledger, BatchContext(batch=batch, products=products, suppliers=suppliers))
        fills.append(result)
        rows = []
        for row_key in result.detail_keys():
            sku = ledger.row(row_key).value("SKU")
            rows.append({"row_key": row_key, "category": str(products[sku].get("category", ""))})
        module4.append(m4.fill_batch(ledger, result.batch_no, rows))
    cross = contract_no_issues([ledger])
    issues: List[Issue] = [issue for fill in fills for issue in fill.issues]
    issues.extend(issue for block in module4 for issue in block.issues)
    issues.extend(cross)
    return {
        "ledger": ledger,
        "fills": fills,
        "module4": module4,
        "invoices": invoices,
        "issues": issues,
        "conversions": [row for fill in fills for row in fill.conversions],
    }