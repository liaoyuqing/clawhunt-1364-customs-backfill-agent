"""离线命令行入口：python -m core.cli --batch B2026-0001 --fixtures fixtures/lingxing --rules rules

一次跑完模块一（采购合同与要件检查）、模块二（批次回填并写入台账）、模块三（发票预览件与合同核对）、
模块四（发票号/耗用/剩余/结余与归档链接回填）的主流程，stdout 只输出一份 JSON 摘要，便于脚本消费；
错误一律走 stderr 并以非 0 退出。

数据来源是仓库自带的合成夹具：LingxingClient + FixtureTransport 回放 fixtures/lingxing/*.json，
不连领星、不连钉钉、不联网。接入真实开放平台时，只需给 LingxingClient 换一个 transport，
本入口的参数、流程与摘要字段不变。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml

from .config import ConfigError, Rules
from .contract import Contract, ContractLine, build_contract, requirement_issues
from .declare import BatchContext, BatchFiller, contract_no_issues
from .dingtalk import DingTalkSheet
from .gates import HumanApprovalRequired, HumanGate
from .invoice import compare_many
from .knowledge import DeclareNameBook
from .ledger import Ledger
from .lingxing import FixtureTransport, LingxingClient, product_declare_fields
from .module4 import InvoiceLedger, Module4Filler
from .units import base_unit, quantity_text

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

EXIT_OK = 0
EXIT_BATCH_MISSING = 1
EXIT_CONFIG_ERROR = 2

PAGE_SIZE = 7
GATE_ACTION = "tax_bureau_submit"

# 全部虚构的买方抬头，写死在入口里只为让离线自验可复现，不涉及任何真实企业。
BUYER = {
    "buyer_name": "深圳前海跨境电子商务有限公司",
    "buyer_tax_no": "91440300MA5F0000XA",
    "invoice_type": "增值税专用发票",
}


class BatchNotFound(LookupError):
    """回放夹具里没有这个批次号。"""


class FixtureError(RuntimeError):
    """夹具目录缺失、不是合法 JSON 或字段不全。"""


def _read_json(path: str) -> Any:
    if not os.path.exists(path):
        raise FixtureError("夹具文件不存在：%s" % path)
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        raise FixtureError("夹具不是合法 JSON：%s（%s）" % (path, exc))


def _chunks(items: List[Dict[str, Any]], size: int) -> List[List[Dict[str, Any]]]:
    return [items[index:index + size] for index in range(0, len(items), size)]


def load_replay(fixtures_dir: str) -> Dict[str, List[List[Dict[str, Any]]]]:
    """把三份夹具按固定页大小切片，交给录像桩回放，顺带覆盖翻页路径。"""
    pages: Dict[str, List[List[Dict[str, Any]]]] = {}
    for name in ("batches", "products", "suppliers"):
        items = _read_json(os.path.join(fixtures_dir, name + ".json"))
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise FixtureError("夹具 %s.json 必须是由对象组成的数组" % name)
        """开放平台列表接口每条记录都带 id；夹具里批次缺 id 时按序号补一个，
        否则分页去重会把不同页当成同一页。"""
        records = []
        for index, item in enumerate(items):
            record = dict(item)
            record.setdefault("id", index + 1)
            records.append(record)
        pages[name] = _chunks(records, PAGE_SIZE)
    return pages


def fetch_all(pages: Dict[str, List[List[Dict[str, Any]]]], resource: str) -> List[Dict[str, Any]]:
    """经 LingxingClient + FixtureTransport 取全量，走的是与真接口相同的分页契约。"""
    client = LingxingClient(FixtureTransport(pages), page_size=PAGE_SIZE)
    return client.fetch_all(resource)


def select_batch(batches: List[Dict[str, Any]], batch_no: str) -> Dict[str, Any]:
    target = str(batch_no)
    for batch in batches:
        if str(batch.get("batch_no")) == target:
            return batch
    sample = [str(batch.get("batch_no")) for batch in batches[:3]]
    raise BatchNotFound(
        "批次不存在：%s（回放夹具共 %d 个批次，例如 %s）" % (target, len(batches), "、".join(sample)),
    )


def build_batch_contract(
    batch: Dict[str, Any],
    products: Dict[str, Dict[str, Any]],
    suppliers: Dict[str, Dict[str, Any]],
) -> Tuple[Contract, List[ContractLine]]:
    """模块一：批次明细 -> 采购合同；合同品名 = 开票名 = 报关名。"""
    lines: List[ContractLine] = []
    supplier_name = ""
    for item in batch.get("lines", []):
        sku = str(item.get("sku", ""))
        product = products.get(sku)
        if product is None:
            raise FixtureError("批次 %s 的 SKU %s 不在 fixtures/lingxing/products.json 里" % (batch.get("batch_no"), sku))
        fields = product_declare_fields(product)
        model = fields["型号"]
        lines.append(
            ContractLine(
                sku=sku,
                product_name=fields["产品中文报关名"],
                invoice_name=fields["产品中文报关名"],
                declare_name=fields["产品中文报关名"],
                spec=("型号 %s" % model) if model else "",
                unit=fields["报关单位"],
                qty=quantity_text(item.get("qty", 0)),
                unit_price=str(product.get("purchase_price", "")),
            )
        )
        if not supplier_name:
            supplier = suppliers.get(str(item.get("supplier_id", "")), {})
            supplier_name = str(supplier.get("name", ""))
    contract = build_contract(str(batch.get("contract_no", "")), supplier_name, BUYER["buyer_name"], lines)
    return contract, lines


def contract_view(contract: Contract, line: ContractLine) -> Dict[str, str]:
    """模块三的比对基准：合同首行 + 买方抬头，字段名与 core.invoice.CHECK_FIELDS 对齐。"""
    try:
        amount = (Decimal(line.qty) * Decimal(line.unit_price)).quantize(Decimal("0.01"))
        amount_text = format(amount, "f")
    except Exception:
        amount_text = ""
    view = dict(BUYER)
    view.update(
        {
            "product_name": line.product_name,
            "spec": line.spec,
            "quantity": line.qty,
            "amount": amount_text,
        }
    )
    return view


def check_invoices(
    invoices_dir: str,
    batch_no: str,
    view: Optional[Dict[str, str]],
) -> Dict[str, Any]:
    """模块三：该批次的发票预览件逐份与合同比对，一条不漏全部列出。"""
    if view is None:
        return {"invoices": 0, "clean": 0, "dirty": 0, "differences": 0, "fields": [], "results": [],
                "note": "该批次没有明细行，跳过发票核对"}
    path = os.path.join(invoices_dir, "%s.json" % batch_no)
    if not os.path.exists(path):
        return {"invoices": 0, "clean": 0, "dirty": 0, "differences": 0, "fields": [], "results": [],
                "note": "该批次没有发票预览件夹具：%s 不存在" % path}
    docs = _read_json(path)
    if not isinstance(docs, list):
        raise FixtureError("发票预览件夹具必须是数组：%s" % path)
    checked = compare_many(docs, view, batch_no=batch_no)
    fields = [item["field"] for result in checked for item in result["differences"]]
    return {
        "invoices": len(checked),
        "clean": sum(1 for result in checked if result["clean"]),
        "dirty": sum(1 for result in checked if not result["clean"]),
        "differences": len(fields),
        "fields": fields,
        "results": [
            {
                "invoice_no": result["invoice_no"],
                "clean": result["clean"],
                "fields": [item["field"] for item in result["differences"]],
            }
            for result in checked
        ],
    }


def load_invoice_ledger(invoices_dir: str) -> InvoiceLedger:
    """发票台账（票面数量/开票日期），模块四按品类取票。"""
    path = os.path.join(invoices_dir, "ledger.json")
    records = _read_json(path)
    if not isinstance(records, list):
        raise FixtureError("发票台账必须是数组：%s" % path)
    return InvoiceLedger(records=records)


def run(
    batch_no: str,
    fixtures_dir: str,
    rules_root: Optional[str],
    invoices_dir: str,
) -> Dict[str, Any]:
    rules = Rules(root=rules_root)
    pages = load_replay(fixtures_dir)
    batches = fetch_all(pages, "batches")
    products = {str(item.get("sku")): item for item in fetch_all(pages, "products")}
    suppliers = {str(item.get("id")): item for item in fetch_all(pages, "suppliers")}
    batch = select_batch(batches, batch_no)
    contract, lines = build_batch_contract(batch, products, suppliers)

    """模块一知识库沉淀：报关名首次出现记新条目，再次出现直接复用同一编码。"""
    book = DeclareNameBook(rules.brand_words)
    for sku in sorted(products):
        product = products[sku]
        book.observe(str(product.get("declare_name", "")), str(product.get("hscode", "")))

    ledger = Ledger(rules.columns)
    filled = BatchFiller(rules).fill(ledger, BatchContext(batch=batch, products=products, suppliers=suppliers))

    """模块四：本批次明细行按品类扣发票额度，写后 7 列（含归档共享链接）。"""
    invoice_ledger = load_invoice_ledger(invoices_dir)
    module4 = Module4Filler(rules, invoice_ledger)
    rows = []
    for row_key in filled.detail_keys():
        sku = ledger.row(row_key).value("SKU")
        rows.append({"row_key": row_key, "category": str(products[sku].get("category", ""))})
    module4_result = module4.fill_batch(ledger, filled.batch_no, rows)

    sheet = DingTalkSheet(rules.columns)
    for row in ledger.rows():
        sheet.upsert_row(row.row_key)
        sheet.write_cells(row.row_key, {column: cell.value for column, cell in row.cells.items()}, row)

    sources: Dict[str, int] = {}
    cells = 0
    for row in ledger.rows():
        for cell in row.cells.values():
            cells += 1
            sources[cell.source.kind] = sources.get(cell.source.kind, 0) + 1

    view = contract_view(contract, lines[0]) if lines else None
    invoice = check_invoices(invoices_dir, str(batch.get("batch_no")), view)
    invoice["basis"] = ("合同首行 %s（%s）" % (lines[0].sku, lines[0].product_name)) if lines else ""
    if view is not None:
        invoice["contract_view"] = view

    gate = HumanGate()
    approved = False
    reason = ""
    try:
        gate.require_human(
            GATE_ACTION,
            {"batch_no": str(batch.get("batch_no")), "amount": contract.total_amount()},
            batch_no=str(batch.get("batch_no")),
        )
        approved = True
    except HumanApprovalRequired as exc:
        reason = "无人工确认，外部提交被阻断（载荷摘要 %s）" % exc.digest

    gaps = [issue.code for issue in requirement_issues(contract)]
    cross = contract_no_issues([ledger])
    issues = list(filled.issues) + list(module4_result.issues) + cross
    return {
        "batch_no": str(batch.get("batch_no")),
        "offline": True,
        "rules_versions": rules.versions(),
        "ledger_columns": rules.columns,
        "module1_contract": {
            "contract_no": contract.contract_no,
            "supplier": contract.supplier,
            "buyer": contract.buyer,
            "lines": len(contract.lines),
            "total_amount": contract.total_amount(),
            "requirement_gaps": gaps,
        },
        "module2_fill": {
            "rows": len(ledger.keys()),
            "columns": len(rules.columns),
            "cells": cells,
            "sheet_rows": len(sheet.rows()),
            "issues": len(issues),
            "pending": len(filled.pending()),
            "pending_list": filled.pending(),
            "sources": sources,
            "base_unit": base_unit(rules.unit_doc),
            "conversions": filled.conversions,
        },
        "module3_invoice": invoice,
        "module4_invoice_ledger": {
            "rows": len(module4_result.rows),
            "categories": module4_result.categories,
            "archive_link": module4_result.link,
            "columns": ["开票公司", "发票号", "开票日期", "耗用发票数量", "发票剩余可用数量", "结余", "归档共享链接"],
            "usage": invoice_ledger.snapshot(),
        },
        "module1_name_book": book.status_counts(),
        "human_gate": {
            "action": GATE_ACTION,
            "approved": approved,
            "reason": reason,
            "audit_rows": len(gate.audit.entries),
        },
        "ok": True,
    }


def _force_utf8_stdout() -> None:
    """Windows 控制台默认代码页不是 UTF-8，JSON 摘要固定按 UTF-8 输出。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass


def main(argv: Optional[Sequence[str]] = None) -> int:
    _force_utf8_stdout()
    parser = argparse.ArgumentParser(
        prog="python -m core.cli",
        description="离线跑模块一~四主流程，打印一份 JSON 摘要（不连领星/钉钉，不联网）",
    )
    parser.add_argument("--batch", required=True, help="批次号，例如 B2026-0001")
    parser.add_argument("--fixtures", default=None, help="回放夹具目录，默认 fixtures/lingxing")
    parser.add_argument("--rules", default=None, help="规则包目录，默认仓库内 rules/")
    parser.add_argument("--invoices", default=None, help="发票夹具目录，默认 fixtures/invoices")
    args = parser.parse_args(list(argv) if argv is not None else None)

    fixtures_dir = args.fixtures or os.path.join(ROOT, "fixtures", "lingxing")
    invoices_dir = args.invoices or os.path.join(ROOT, "fixtures", "invoices")

    try:
        summary = run(args.batch, fixtures_dir, args.rules, invoices_dir)
    except ConfigError as exc:
        print("规则包加载失败：%s" % exc, file=sys.stderr)
        return EXIT_CONFIG_ERROR
    except yaml.YAMLError as exc:
        print("规则包解析失败：%s" % exc, file=sys.stderr)
        return EXIT_CONFIG_ERROR
    except FixtureError as exc:
        print("夹具加载失败：%s" % exc, file=sys.stderr)
        return EXIT_CONFIG_ERROR
    except BatchNotFound as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_BATCH_MISSING
    except (KeyError, ValueError, OSError) as exc:
        print("运行失败：%s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return EXIT_CONFIG_ERROR

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())