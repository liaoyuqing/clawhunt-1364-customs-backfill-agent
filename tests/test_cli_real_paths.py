"""买家退返第 1/2/3/5 条：真实调用路径接进 core.cli 的生产链路。

测试直接调 core.cli.run，把 RecordingTransport 注入各客户端，断言：
  1. 视觉模型真的收到 POST，高置信度字段写进台账，低置信度进人工队列；
  2. Computer Use 执行器真的收到动作；
  3. 模块四本地模型真的收到发票原件识别请求；
  4. 电子税务局提交必须先过人工闸门，未确认时一次请求都不发。
"""

from __future__ import annotations

import json
import os

from core import cli
from core.computer_use import ComputerUseClient, ComputerUseConfig
from core.etax import EtaxClient, EtaxConfig
from core.gates import HumanGate
from core.http_client import HttpResponse, RecordingTransport
from core.local_model import LocalModelClient, LocalModelConfig
from core.vision_client import VisionClient, VisionConfig

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "fixtures", "lingxing")
INVOICES = os.path.join(ROOT, "fixtures", "invoices")

"""B2026-0011 的 SKU-1006 在源数据里没有 HSCODE，正好用视觉补位验证「补空格」。"""
BATCH = "B2026-0011"


def _chat_response(content):
    body = json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]}).encode("utf-8")
    return HttpResponse(200, {"Content-Type": "application/json"}, body)


def _json_response(payload, status=200):
    return HttpResponse(status, {"Content-Type": "application/json"}, json.dumps(payload).encode("utf-8"))


def _vision(fields, repeat=8):
    http = RecordingTransport([_chat_response(json.dumps({"fields": fields})) for _ in range(repeat)])
    client = VisionClient(VisionConfig(base_url="http://127.0.0.1:9000", api_key="TEST-ONLY-NOT-REAL"), http=http)
    return client, http


def _computer_use():
    http = RecordingTransport([_json_response({"executed": True, "screenshot_id": "shot-2"}) for _ in range(8)])
    client = ComputerUseClient(ComputerUseConfig(base_url="http://127.0.0.1:9100", api_key="TEST-ONLY-NOT-REAL"), http=http)
    return client, http


def _etax():
    http = RecordingTransport([_json_response({"code": 0, "submit_no": "SUBMIT-SYNTHETIC-0001"})])
    client = EtaxClient(EtaxConfig(base_url="https://etax.example.invalid", token="TEST-ONLY-NOT-REAL"), http=http)
    return client, http


def test_cli_vision_pass_writes_high_confidence_field():
    """视觉补位：真的发 POST，高置信度字段写进台账，来源标记为 vision。"""
    vision, http = _vision({"HSCODE": {"text": "3926909090", "confidence": 0.96}})
    summary = cli.run(BATCH, FIXTURES, None, INVOICES, vision_client=vision, vision_image_template="http://127.0.0.1:9000/shot-{row_key}.png")
    assert len(http.requests) >= 1
    request = http.requests[0]
    assert request.method == "POST"
    assert request.url == "http://127.0.0.1:9000/v1/chat/completions"
    assert request.describe()["headers"]["Authorization"] == "***"
    assert summary["module2_vision"]["calls"] >= 1
    assert summary["module2_vision"]["written"] >= 1
    assert summary["module2_vision"]["manual"] == 0


def test_cli_vision_low_confidence_goes_to_manual_queue():
    """低置信度字段不写台账，全部进人工队列。"""
    vision, http = _vision({"HSCODE": {"text": "3926909090", "confidence": 0.42}})
    summary = cli.run(BATCH, FIXTURES, None, INVOICES, vision_client=vision, vision_image_template="http://127.0.0.1:9000/shot-{row_key}.png")
    assert summary["module2_vision"]["calls"] >= 1
    assert summary["module2_vision"]["written"] == 0
    assert summary["module2_vision"]["manual"] >= 1
    manual = summary["module2_vision"]["manual_list"][0]
    assert manual["name"] == "HSCODE"
    assert manual["needs_manual"] is True


def test_cli_computer_use_receives_action():
    """Computer Use：视觉结果转成动作后，执行器真的收到 POST。"""
    vision, _vh = _vision({"HSCODE": {"text": "3926909090", "confidence": 0.96}})
    cu, cu_http = _computer_use()
    summary = cli.run(
        BATCH, FIXTURES, None, INVOICES,
        vision_client=vision,
        vision_image_template="http://127.0.0.1:9000/shot-{row_key}.png",
        computer_use_client=cu,
    )
    assert summary["module2_computer_use"]["actions"] >= 1
    assert len(cu_http.requests) >= 1
    assert cu_http.requests[0].url == "http://127.0.0.1:9100/v1/computer/actions"
    assert cu_http.requests[0].describe()["headers"]["Authorization"] == "***"


def test_cli_local_model_recognizes_invoice():
    """模块四：本地模型真的收到发票原件识别请求，号码一致才写台账。"""
    local_http = RecordingTransport([_chat_response(json.dumps({
        "invoice_no": "INV-2026-HOME-01",
        "issued_at": "2026-11-02",
        "buyer_name": "深圳前海跨境电子商务有限公司",
        "buyer_tax_no": "91440300MA5F0000XA",
        "amount": "9.60",
        "invoice_type": "增值税专用发票",
    })) for _ in range(8)])
    local = LocalModelClient(LocalModelConfig(base_url="http://127.0.0.1:8000"), http=local_http)
    summary = cli.run(
        BATCH, FIXTURES, None, INVOICES,
        local_model_client=local,
        local_model_image_template="http://127.0.0.1:8000/invoice-{row_key}.png",
    )
    assert summary["module4_local_model"]["calls"] >= 1
    assert len(local_http.requests) >= 1
    assert local_http.requests[0].url == "http://127.0.0.1:8000/v1/chat/completions"
    payload = local_http.requests[0].body_json()
    assert payload["messages"][0]["content"][1]["image_url"]["url"].startswith("http://127.0.0.1:8000/invoice-")


def test_cli_etax_without_gate_never_submits():
    """默认闸门无人值守：电子税务局草稿照出，但一次提交请求都不发。"""
    etax, etax_http = _etax()
    summary = cli.run(BATCH, FIXTURES, None, INVOICES, etax_client=etax)
    assert summary["module3_etax"]["submitted"] is False
    assert summary["module3_etax"]["draft"]["batch_no"] == BATCH
    assert "未获人工确认" in summary["module3_etax"]["reason"]
    assert len(etax_http.requests) == 0, "未确认时不得发出任何提交请求"


def test_cli_etax_with_approval_submits_once():
    """人工确认后提交一次，载荷含发票号与税号，审计行落盘可按批次检索。"""
    etax, etax_http = _etax()
    gate = HumanGate(approver=lambda action, payload: "审核员张三")
    summary = cli.run(BATCH, FIXTURES, None, INVOICES, etax_client=etax, gate=gate)
    assert summary["module3_etax"]["submitted"] is True
    assert summary["module3_etax"]["submit_no"] == "SUBMIT-SYNTHETIC-0001"
    assert len(etax_http.requests) == 1
    body = etax_http.requests[0].body_json()
    assert body["buyer_tax_no"] == "91440300MA5F0000XA"
    assert body["invoice_no"]
    assert gate.audit.by_batch(BATCH)


def test_cli_without_real_clients_stays_offline():
    """不传任何真实客户端时，四条真实路径都不跑，摘要里如实记 0。"""
    summary = cli.run(BATCH, FIXTURES, None, INVOICES)
    assert summary["module2_vision"]["calls"] == 0
    assert summary["module2_computer_use"]["actions"] == 0
    assert summary["module4_local_model"]["calls"] == 0
    assert summary["module3_etax"]["submitted"] is False
    assert summary["offline"] is True
