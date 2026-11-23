"""买家退返第 2 条：真实视觉模型调用路径 + Computer Use 界面操作。

测试用 RecordingTransport 记录真正发出的 HTTP 请求：URL、method、鉴权头名（值脱敏）、
body 结构。低置信度字段必须转人工队列且不写台账，高置信度字段自动写入台账。
"""

from __future__ import annotations

import json
import os

from core.computer_use import (
    ComputerAction,
    ComputerUseClient,
    ComputerUseConfig,
    Screenshot,
    plan_actions,
)
from core.http_client import HttpResponse, RecordingTransport
from core.ledger import Ledger
from core.models import SourceRef
from core.vision_client import (
    VisionClient,
    VisionConfig,
    build_payload,
    data_uri,
    parse_fields,
    to_vision_fields,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECORDED = os.path.join(ROOT, "fixtures", "recorded", "vision_response.json")


def _response(content):
    body = json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]}).encode("utf-8")
    return HttpResponse(200, {"Content-Type": "application/json"}, body)


def _config():
    return VisionConfig(base_url="http://127.0.0.1:9000", api_key="TEST-ONLY-NOT-REAL", model="vision-latest")


def test_vision_client_sends_real_http_request_with_image_url():
    """证据落在「真的发出了 HTTP 请求」这一层：URL、method、鉴权头名、body 结构都记得到。"""
    transport = RecordingTransport([_response(json.dumps({"fields": {"SKU": {"text": "SKU-1001", "confidence": 0.96}}}))])
    client = VisionClient(_config(), http=transport)
    result = client.recognize(data_uri(b"fake-png-bytes", mime="image/png"))
    assert result["fields"]["SKU"]["text"] == "SKU-1001"
    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.method == "POST"
    assert request.url == "http://127.0.0.1:9000/v1/chat/completions"
    described = request.describe()
    assert described["headers"]["Authorization"] == "***", "鉴权头的值必须脱敏"
    assert "Authorization" in described["headers"]
    assert sorted(described["body_keys"]) == ["messages", "model", "temperature"]
    payload = request.body_json()
    content = payload["messages"][0]["content"]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_vision_low_confidence_goes_to_manual_queue_and_is_not_written():
    """低置信度字段必须转人工，且不写台账。"""
    payload = {"fields": {"SKU": {"text": "SKU-1001", "confidence": 0.96}, "数量": {"text": "8", "confidence": 0.62}}}
    transport = RecordingTransport([_response(json.dumps(payload))])
    client = VisionClient(_config(), http=transport)
    result = client.recognize(data_uri(b"fake-png-bytes"))
    fields = to_vision_fields(result, "shot-1", 0.85)
    ledger = Ledger(["SKU", "数量"])
    ledger.ensure_row("B1#0", "B1")
    manual = []
    for field in fields:
        if field["needs_manual"]:
            manual.append(field)
            continue
        ledger.write("B1#0", field["name"], field["text"], SourceRef(kind="vision", ref="shot-1", at="2026-11-23T00:00:00+08:00"))
    assert [item["name"] for item in manual] == ["数量"]
    assert ledger.row("B1#0").value("SKU") == "SKU-1001"
    assert ledger.row("B1#0").get("数量") is None, "低置信度字段不得写台账"


def test_computer_use_client_sends_action_with_screenshot():
    """界面操作路径：真的 POST 到 /v1/computer/actions，body 含动作与截图 data URI。"""
    body = json.dumps({"executed": True, "screenshot": {"screenshot_id": "shot-2"}}).encode("utf-8")
    transport = RecordingTransport([HttpResponse(200, {}, body)])
    client = ComputerUseClient(
        ComputerUseConfig(base_url="http://127.0.0.1:9100", api_key="TEST-ONLY-NOT-REAL"),
        http=transport,
    )
    answer = client.execute(
        ComputerAction(kind="type", target="SKU", text="SKU-1001", screenshot_id="shot-1"),
        Screenshot(screenshot_id="shot-1", image_bytes=b"fake-png-bytes", width=800, height=600),
    )
    assert answer["executed"] is True
    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.url == "http://127.0.0.1:9100/v1/computer/actions"
    assert request.method == "POST"
    assert request.describe()["headers"]["Authorization"] == "***"
    payload = request.body_json()
    assert payload["action"]["kind"] == "type"
    assert payload["action"]["text"] == "SKU-1001"
    assert payload["screenshot"].startswith("data:image/png;base64,")


def test_plan_actions_splits_by_confidence():
    payload = {"screenshot_id": "shot-3", "fields": {"SKU": {"text": "SKU-1002", "confidence": 0.93}, "规格": {"text": "M-1002", "confidence": 0.40}}}
    actions, manual = plan_actions(payload, threshold=0.85)
    assert [action.target for action in actions] == ["SKU"]
    assert [item["name"] for item in manual] == ["规格"]
    assert "转人工" in manual[0]["reason"]


def test_recorded_vision_response_is_marked_synthetic():
    """录制文件必须写明来源：本地合成，不是厂商接口抓包。"""
    with open(RECORDED, encoding="utf-8") as handle:
        doc = json.load(handle)
    assert doc["_provenance"] == "local synthetic replay, not a capture from the vendor API"
    assert "scripts/recorded_smoke.py --source live" in doc["_note"]


def test_parse_fields_requires_fields_object():
    assert parse_fields(json.dumps({"fields": {"SKU": {"text": "x", "confidence": 1}}}))["fields"]["SKU"]["text"] == "x"
    try:
        parse_fields(json.dumps({"result": "no fields"}))
    except Exception as exc:
        assert "fields" in str(exc)
    else:
        raise AssertionError("缺 fields 对象时必须报错")


def test_build_payload_is_openai_compatible():
    payload = build_payload("data:image/png;base64,AAAA", "prompt", "vision-latest")
    assert payload["model"] == "vision-latest"
    assert payload["messages"][0]["role"] == "user"
    assert payload["messages"][0]["content"][0]["text"] == "prompt"
