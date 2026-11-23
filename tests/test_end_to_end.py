"""端到端：一个批次从回填到归档的完整链路，含人工闸门与共享链接回填。"""

from __future__ import annotations

from core.archive import ArchiveStore, InvoiceUsage
from core.contract import ContractLine, build_contract
from core.declare import BatchContext, BatchFiller
from core.dingtalk import DingTalkSheet
from core.gates import HumanApprovalRequired, HumanGate
from core.invoice import build_notice, compare_many
from core.ledger import Ledger


def test_end_to_end_batch_to_archive(tmp_path, rules, products, suppliers):
    batch = {
        "batch_no": "E2E-0001",
        "shipment_no": "FBA-SF-E2E1",
        "contract_no": "HT-E2E-0001",
        "lines": [
            {"sku": "BND-2S", "qty": 4, "cartons": 8, "weight_kg": "1.10", "supplier_id": "S01",
             "purchase_qty": 4, "shipment_qty": 4},
        ],
    }
    ledger = Ledger(rules.columns)
    result = BatchFiller(rules).fill(ledger, BatchContext(batch=batch, products=products, suppliers=suppliers))
    assert len(result.row_keys) == 3

    sheet = DingTalkSheet(rules.columns)
    for row in ledger.rows():
        sheet.upsert_row(row.row_key)
        sheet.write_cells(row.row_key, {c: cell.value for c, cell in row.cells.items()}, row)
    assert len(sheet.writes) == 3
    assert sheet.read_row("E2E-0001")["批次号"] == "E2E-0001"

    contract = build_contract(
        "HT-E2E-0001",
        "深圳宝安收纳制品厂",
        "客户公司",
        [ContractLine(sku="SKU-1001", product_name="桌面收纳盒", invoice_name="桌面收纳盒",
                      declare_name="桌面收纳盒", spec="型号 M-1001", unit="个", qty="8", unit_price="12.80")],
    )
    assert contract.total_amount() == "102.40"

    notice = build_notice("E2E-0001", "家居", rules.invoice_companies, [
        {"sku": "SKU-1001", "name": "桌面收纳盒", "qty": "8", "price": "12.80"},
    ])
    assert notice.company == "深圳前海家居供应链有限公司"

    contract_view = {
        "buyer_name": "客户公司", "buyer_tax_no": "91440300MA5F0001XA",
        "invoice_type": "增值税专用发票", "product_name": "桌面收纳盒",
        "spec": "型号 M-1001", "quantity": "8", "amount": "102.40",
    }
    invoices = [
        dict(contract_view, invoice_no="INV-E2E-1"),
        dict(contract_view, invoice_no="INV-E2E-2", buyer_tax_no="91440300MA5F0000XX"),
    ]
    checked = compare_many(invoices, contract_view, batch_no="E2E-0001")
    assert [item["clean"] for item in checked] == [True, False]
    assert checked[1]["differences"][0]["field"] == "税号"

    usage = InvoiceUsage(invoice_no="INV-E2E-1", invoiced_total="102.40")
    assert usage.use("E2E-0001", "102.40") == "0.00"

    store = ArchiveStore(str(tmp_path))
    for kind, name in (("合同", "contract.pdf"), ("送货单", "delivery.pdf"), ("发票原件", "invoice.pdf"), ("对公支付凭证", "payment.pdf")):
        store.put("E2E-0001", kind, name, kind.encode("utf-8"))
    ledger.write("E2E-0001", "归档共享链接", store.link("E2E-0001"), __import__("core.models", fromlist=["SourceRef"]).SourceRef(
        kind="api", ref="archive/local", at="2026-11-23T00:00:00+08:00"))
    assert ledger.row("E2E-0001").value("归档共享链接").startswith("https://")

    gate = HumanGate(approver=None)
    try:
        gate.require_human("tax_bureau_submit", {"batch_no": "E2E-0001"})
        raise AssertionError("no approval must block")
    except HumanApprovalRequired:
        pass
    gate_ok = HumanGate(approver=lambda action, payload: "bobo")
    approval = gate_ok.require_human("tax_bureau_submit", {"batch_no": "E2E-0001"}, batch_no="E2E-0001")
    assert approval.operator == "bobo"
    assert gate_ok.audit.by_batch("E2E-0001")[0]["digest"] == approval.digest