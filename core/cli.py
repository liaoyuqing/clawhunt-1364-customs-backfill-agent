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
from .computer_use import ComputerUseClient, ComputerUseConfig, ComputerUseConfigError
from .contract import Contract, ContractLine, build_contract, requirement_issues
from .declare import BatchContext, BatchFiller, contract_no_issues
from .dingtalk import DingTalkSheet
from .etax import (
    EtaxClient,
    EtaxConfig,
    EtaxConfigError,
    build_draft,
    pending_submissions,
    submit_with_gate,
)
from .gates import HumanApprovalRequired, HumanGate
from .invoice import compare_many
from .knowledge import DeclareNameBook, default_db_path
from .local_model import (
    LocalModelBlocked,
    LocalModelClient,
    LocalModelConfig,
    LocalModelConfigError,
    assert_local_allowed,
)
from .ledger import Ledger
from .lingxing import FixtureTransport, LingxingClient, product_declare_fields
from .module4 import InvoiceLedger, Module4Filler
from .pipeline import computer_actions
from .vision import apply_vision_fields
from .vision_client import VisionClient, VisionConfig, VisionConfigError, to_vision_fields
from .sources import SOURCES, SOURCE_FIXTURE, SourceError, SheetSink, load_source
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


def _vision_rows(ledger, row_keys):
    """挑出这些行里还空着的格子（视觉补位只补空格）。"""
    out = []
    for row_key in row_keys:
        row = ledger.row(row_key)
        for column in ledger.columns:
            if row.get(column) is None:
                out.append((row_key, column))
    return out


def _template_image(template: str, batch_no: str, row_key: str) -> str:
    """图片地址模板：把 {batch_no} 与 {row_key} 换成实际的批次号和明细行键。"""
    try:
        return template.format(batch_no=batch_no, row_key=row_key)
    except (KeyError, IndexError, ValueError):
        return template


def run_vision_pass(
    ledger,
    row_keys,
    client,
    image_template,
    at="",
):
    """真实视觉调用一轮：识别 -> 写台账 -> 低置信度进人工队列。

    client 是 core.vision_client.VisionClient；image_template 是图片地址模板。
    返回 {"calls", "written", "manual", "results"}。
    """
    from .vision_client import to_vision_fields

    written = []
    manual = []
    results = []
    calls = 0
    for row_key in row_keys:
        image_url = _template_image(image_template, ledger.row(row_key).batch_no, row_key)
        if not image_url:
            continue
        calls += 1
        result = client.recognize(image_url)
        fields = to_vision_fields(result, "vision-%s" % row_key, client.config.threshold)
        hit, pending = apply_vision_fields(ledger, row_key, fields, at=at)
        for item in hit:
            item["row_key"] = row_key
        for item in pending:
            item["row_key"] = row_key
        written.extend(hit)
        manual.extend(pending)
        results.append({"row_key": row_key, "fields": len(fields), "written": len(hit)})
    return {"calls": calls, "written": written, "manual": manual, "results": results}


def run_computer_use_pass(client, vision_payloads, at=""):
    """Computer Use：视觉结果 -> 动作 -> 真的发给执行器。

    返回 {"actions", "executed", "manual", "results"}。
    """
    executed = []
    manual = []
    results = []
    for payload in vision_payloads:
        actions, pending = computer_actions(payload)
        manual.extend(pending)
        for action in actions:
            answer = client.execute(action)
            executed.append(action.as_dict())
            results.append({"action": action.as_dict(), "answer": answer})
    return {"actions": len(executed), "executed": executed, "manual": manual, "results": results}
def run(
    batch_no: str,
    fixtures_dir: str,
    rules_root: Optional[str],
    invoices_dir: str,
    source: str = SOURCE_FIXTURE,
    knowledge_db: Optional[str] = None,
    sheet_sink: Optional[SheetSink] = None,
    vision_client: Optional[Any] = None,
    vision_image_template: str = "",
    computer_use_client: Optional[Any] = None,
    local_model_client: Optional[Any] = None,
    local_model_image_template: str = "",
    etax_client: Optional[Any] = None,
    declaration_date: str = "",
    gate: Optional[Any] = None,
) -> Dict[str, Any]:
    """跑一个批次的模块一~四主流程。

    source 决定数据从哪来：fixture（仓库夹具，默认）/ recorded（本地合成录制回放）/
    lingxing（真实领星开放平台，凭据只从环境变量读）/ dingtalk（台账直连真实钉钉）。
    knowledge_db 非空时模块一知识库落盘，重启后仍能读回。

    vision_client / computer_use_client / local_model_client / etax_client 非空时，
    对应的真实调用路径才会跑起来；默认全为空，行为与纯离线自验完全一致。
    """
    rules = Rules(root=rules_root)
    if str(source) == SOURCE_FIXTURE:
        pages = load_replay(fixtures_dir)
        batches = fetch_all(pages, "batches")
        products = {str(item.get("sku")): item for item in fetch_all(pages, "products")}
        suppliers = {str(item.get("id")): item for item in fetch_all(pages, "suppliers")}
        provenance = {
            "source": SOURCE_FIXTURE,
            "origin": "synthetic fixtures in this repository",
            "files": [
                os.path.join(fixtures_dir, name + ".json")
                for name in ("batches", "products", "suppliers")
            ],
            "synthetic": True,
        }
    else:
        batches, products, suppliers, provenance = load_source(source)
    batch = select_batch(batches, batch_no)
    contract, lines = build_batch_contract(batch, products, suppliers)

    """模块一知识库沉淀：报关名首次出现记新条目，再次出现直接复用同一编码。

    knowledge_db 非空时知识库落盘到该文件（进程重启后仍能读回）；确认过的品名
    用确认编码写进台账，没确认的按开关决定是否写入并报 name_not_confirmed。
    """
    book = DeclareNameBook(rules.brand_words, path=knowledge_db)
    for sku in sorted(products):
        product = products[sku]
        book.observe(
            str(product.get("declare_name", "")),
            str(product.get("hscode", "")),
            material=str(product.get("material", "")),
            usage=str(product.get("usage", "")),
            unit=str(product.get("declare_unit", "")),
        )

    ledger = Ledger(rules.columns)
    filled = BatchFiller(
        rules,
        name_book=book,
        gate_unconfirmed_names=bool(knowledge_db),
    ).fill(ledger, BatchContext(batch=batch, products=products, suppliers=suppliers))

    """模块四：本批次明细行按品类扣发票额度，写后 7 列（含归档共享链接）。"""
    invoice_ledger = load_invoice_ledger(invoices_dir)
    module4 = Module4Filler(rules, invoice_ledger, local_model=local_model_client)
    rows = []
    for row_key in filled.detail_keys():
        sku = ledger.row(row_key).value("SKU")
        rows.append({"row_key": row_key, "category": str(products[sku].get("category", ""))})
    module4_result = module4.fill_batch(ledger, filled.batch_no, rows)

    """接了本地模型时：发票原件由本地模型识别，号码与台账一致才写回。"""
    local_model_results: List[Dict[str, Any]] = []
    if local_model_client is not None:
        items = []
        for item in rows:
            sku = ledger.row(item["row_key"]).value("SKU")
            image_url = _template_image(local_model_image_template, filled.batch_no, item["row_key"])
            if not image_url:
                continue
            items.append({"row_key": item["row_key"], "category": item["category"], "image_url": image_url})
        if items:
            local_model_results = module4.recognize_invoices(ledger, items)

    """真实视觉调用：只补还空着的格，低置信度转人工队列。"""
    vision_summary: Dict[str, Any] = {"calls": 0, "written": [], "manual": [], "results": []}
    vision_payloads: List[Dict[str, Any]] = []
    if vision_client is not None and vision_image_template:
        vision_summary = run_vision_pass(ledger, filled.detail_keys(), vision_client, vision_image_template)
        payloads: Dict[str, Dict[str, Any]] = {}
        for field in vision_summary["written"]:
            row_key = str(field.get("row_key", ""))
            if not row_key:
                continue
            payload = payloads.setdefault(row_key, {"screenshot_id": "vision-%s" % row_key, "fields": {}})
            payload["fields"][str(field.get("name"))] = {
                "text": str(field.get("text", "")),
                "confidence": float(field.get("confidence", 0.0)),
            }
        vision_payloads = [payloads[row_key] for row_key in sorted(payloads)]

    """Computer Use：视觉结果转成动作，真的发给界面操作执行器。"""
    computer_summary: Dict[str, Any] = {"actions": 0, "executed": [], "manual": [], "results": []}
    if computer_use_client is not None and vision_payloads:
        computer_summary = run_computer_use_pass(computer_use_client, vision_payloads)

    sheet = DingTalkSheet(rules.columns)
    for row in ledger.rows():
        sheet.upsert_row(row.row_key)
        sheet.write_cells(row.row_key, {column: cell.value for column, cell in row.cells.items()}, row)

    """台账写回：给了真实钉钉客户端就发 HTTP 写行，否则只落内存表。"""
    sheet_written: List[str] = []
    if sheet_sink is not None:
        for row in ledger.rows():
            values = {column: cell.value for column, cell in row.cells.items()}
            sheet_sink.write_row(row.row_key, values)
            sheet_written.append(row.row_key)

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

    """人工闸门：默认无人值守（任何对外动作都被阻断）；测试或人工值守时可注入。"""
    gate = gate if gate is not None else HumanGate()
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

    """模块三电子税务局半自动填单：先生成草稿，提交与否一律走人工闸门。

    草稿的发票字段优先取本批次实际核对的预览件，没有预览件时退回发票台账里
    该品类的票号与开票日期，保证草稿里不会出现空票号。
    """
    etax_view = dict(invoice.get("contract_view") or {})
    if not etax_view.get("invoice_no"):
        first_category = str(products.get(str(lines[0].sku), {}).get("category", "")) if lines else ""
        try:
            record = invoice_ledger.pick(first_category)
        except Exception:
            record = {}
        if record:
            etax_view.setdefault("invoice_no", str(record.get("invoice_no", "")))
            etax_view.setdefault("issued_at", str(record.get("issued_at", "")))
            etax_view.setdefault("invoice_type", str(record.get("invoice_type", "")))
    draft = build_draft(
        str(batch.get("batch_no")),
        contract,
        etax_view,
        lines=lines,
        declaration_date=declaration_date,
    )
    if etax_client is not None:
        etax_summary = dict(submit_with_gate(gate, etax_client, draft, batch_no=str(batch.get("batch_no"))))
        etax_summary["draft"] = draft
    else:
        etax_summary = {
            "draft": draft,
            "submitted": False,
            "reason": "没有可用的电子税务局客户端，只产出草稿与待提交清单",
            "pending": pending_submissions([draft]),
        }

    gaps = [issue.code for issue in requirement_issues(contract)]
    cross = contract_no_issues([ledger])
    issues = list(filled.issues) + list(module4_result.issues) + list(module4.issues) + cross
    return {
        "batch_no": str(batch.get("batch_no")),
        "offline": str(source) != "lingxing",
        "source": provenance,
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
        "module1_name_book": dict(book.status_counts(), persisted=book.persisted, db=book.path or ""),
        "module2_vision": {
            "calls": int(vision_summary.get("calls", 0)),
            "written": len(vision_summary.get("written", [])),
            "manual": len(vision_summary.get("manual", [])),
            "manual_list": vision_summary.get("manual", []),
            "results": vision_summary.get("results", []),
        },
        "module2_computer_use": {
            "actions": int(computer_summary.get("actions", 0)),
            "executed": computer_summary.get("executed", []),
            "manual": len(computer_summary.get("manual", [])),
            "results": computer_summary.get("results", []),
        },
        "module4_local_model": {
            "calls": len(local_model_results),
            "results": local_model_results,
            "issues": [issue.code for issue in module4.issues],
        },
        "module3_etax": etax_summary,
        "sheet_written": sheet_written,
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
    parser.add_argument(
        "--source",
        default=SOURCE_FIXTURE,
        choices=list(SOURCES),
        help="数据来源：fixture（仓库夹具，默认）/ recorded（本地合成录制回放）/ lingxing（真实领星 API）",
    )
    parser.add_argument(
        "--knowledge-db",
        default=None,
        help="模块一知识库落盘路径，默认不落盘；不传时用环境变量 CLAWHUNT_KNOWLEDGE_DB",
    )
    parser.add_argument(
        "--write-sheet",
        action="store_true",
        help="写回真实钉钉表格（需要 DINGTALK_* 环境变量），默认只落内存表",
    )
    parser.add_argument(
        "--vision-image",
        default="",
        help="视觉补位图片地址模板（支持 {batch_no}/{row_key}），需要 VISION_BASE_URL",
    )
    parser.add_argument(
        "--computer-use",
        action="store_true",
        help="启用界面操作执行器（需要 COMPUTER_USE_BASE_URL），与 --vision-image 配套",
    )
    parser.add_argument(
        "--local-model-image",
        default="",
        help="模块四发票原件图片地址模板，需要 LOCAL_MODEL_BASE_URL（地址必须过内网白名单）",
    )
    parser.add_argument(
        "--egress-whitelist",
        default="",
        help="出网白名单，逗号分隔的 CIDR；与 --local-model-image 配套，模块四本地模型调用前校验",
    )
    parser.add_argument(
        "--etax",
        action="store_true",
        help="启用电子税务局对接（需要 ETAX_BASE_URL），提交仍需人工闸门确认",
    )
    parser.add_argument(
        "--declaration-date",
        default="",
        help="申报单草稿的申报日期，默认取发票开具日期",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    fixtures_dir = args.fixtures or os.path.join(ROOT, "fixtures", "lingxing")
    invoices_dir = args.invoices or os.path.join(ROOT, "fixtures", "invoices")
    knowledge_db = args.knowledge_db if args.knowledge_db else (os.environ.get("CLAWHUNT_KNOWLEDGE_DB") or None)

    sink = None
    if args.write_sheet:
        try:
            from .sources import sheet_sink_from_env

            sink = sheet_sink_from_env()
        except SourceError as exc:
            print("钉钉台账写入失败：%s" % exc, file=sys.stderr)
            return EXIT_CONFIG_ERROR

    """真实调用路径的客户端：只在对应开关打开时创建，凭据全部来自环境变量。"""
    vision_client = None
    if args.vision_image:
        try:
            vision_client = VisionClient(VisionConfig.from_env())
        except VisionConfigError as exc:
            print("视觉模型配置失败：%s" % exc, file=sys.stderr)
            return EXIT_CONFIG_ERROR

    computer_use_client = None
    if args.computer_use:
        try:
            computer_use_client = ComputerUseClient(ComputerUseConfig.from_env())
        except ComputerUseConfigError as exc:
            print("界面操作执行器配置失败：%s" % exc, file=sys.stderr)
            return EXIT_CONFIG_ERROR

    local_model_client = None
    if args.local_model_image:
        try:
            local_model_client = LocalModelClient(LocalModelConfig.from_env())
        except LocalModelConfigError as exc:
            print("本地模型配置失败：%s" % exc, file=sys.stderr)
            return EXIT_CONFIG_ERROR
        whitelist = [item.strip() for item in str(args.egress_whitelist).split(",") if item.strip()]
        try:
            assert_local_allowed(local_model_client.config, whitelist or None)
        except LocalModelBlocked as exc:
            print("本地模型地址未通过内网校验：%s" % exc, file=sys.stderr)
            return EXIT_CONFIG_ERROR

    etax_client = None
    if args.etax:
        try:
            etax_client = EtaxClient(EtaxConfig.from_env())
        except EtaxConfigError as exc:
            print("电子税务局配置失败：%s" % exc, file=sys.stderr)
            return EXIT_CONFIG_ERROR

    try:
        summary = run(
            args.batch,
            fixtures_dir,
            args.rules,
            invoices_dir,
            source=args.source,
            knowledge_db=knowledge_db,
            sheet_sink=sink,
            vision_client=vision_client,
            vision_image_template=args.vision_image,
            computer_use_client=computer_use_client,
            local_model_client=local_model_client,
            local_model_image_template=args.local_model_image,
            etax_client=etax_client,
            declaration_date=args.declaration_date,
        )
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
    except SourceError as exc:
        print("数据源不可用：%s" % exc, file=sys.stderr)
        return EXIT_CONFIG_ERROR
    except (KeyError, ValueError, OSError) as exc:
        print("运行失败：%s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return EXIT_CONFIG_ERROR

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())