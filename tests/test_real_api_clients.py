"""买家退返第 1 条：真实领星 / 钉钉客户端必须真的发起 HTTP 调用。

证据落在「请求被发出并被解析」这一层：URL、方法、鉴权头名（值脱敏）、body 结构。
"""

from __future__ import annotations

import json
import os

from core.dingtalk_client import DingTalkClient, DingTalkConfig, DingTalkConfigError
from core.http_client import HttpResponse, RateLimitError, RecordingTransport
from core.lingxing_client import (
    ENV_ACCESS_TOKEN,
    ENV_APP_KEY,
    ENV_APP_SECRET,
    ENV_BASE_URL,
    LingxingApiClient,
    LingxingConfig,
    LingxingConfigError,
    LingxingTransport,
)


def _lingxing_page(items, total=None):
    body = json.dumps({
        "code": 0,
        "msg": "ok",
        "data": {"list": items, "total": total if total is not None else len(items)},
    }).encode("utf-8")
    return HttpResponse(200, {"Content-Type": "application/json"}, body)


def _client(http, **overrides):
    settings = {
        "base_url": "https://lingxing.example.invalid",
        "app_key": "APP-SYNTHETIC-NOT-REAL",
        "app_secret": "SECRET-SYNTHETIC-NOT-REAL",
        "access_token": "TOKEN-SYNTHETIC-NOT-REAL",
    }
    settings.update(overrides)
    transport = LingxingTransport(
        base_url=settings["base_url"],
        app_key=settings["app_key"],
        app_secret=settings["app_secret"],
        access_token=settings["access_token"],
        http=http,
    )
    return LingxingApiClient(LingxingConfig(**settings), transport=transport, sleep=lambda seconds: None)


def test_repository_contains_no_real_looking_credentials():
    """仓库里不能出现像真实凭据的字面量。

    只看「键 = 字面量」这一种形式：JSON/YAML 的 "app_secret": "xxx" 与 Python 的
    app_secret="xxx"。取值必须为空串或明显的示意形态。
    tests/ 目录不参与扫描：测试代码里必然出现键名与示意值本身。
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    markers = [
        "\"app_secret\":", "\"appSecret\":", "\"app_key\":", "\"appKey\":",
        "\"access_token\":", "\"accessToken\":", "\"token\":",
        "app_secret=", "appSecret=", "app_key=", "appKey=",
        "access_token=", "accessToken=", "token=",
    ]
    suspicious = []
    for folder, _dirs, files in os.walk(root):
        parts = folder.replace(root, "").split(os.sep)
        if ".venv" in parts or ".git" in parts or "__pycache__" in parts or "tests" in parts:
            continue
        for name in files:
            if not name.endswith((".py", ".json", ".yaml", ".yml", ".md", ".txt", ".sh")):
                continue
            path = os.path.join(folder, name)
            with open(path, encoding="utf-8", errors="replace") as handle:
                text = handle.read()
            for marker in markers:
                index = text.find(marker)
                while index != -1:
                    tail = text[index + len(marker):index + len(marker) + 80]
                    value = ""
                    if tail.startswith(chr(34)):
                        rest = tail[1:]
                        end = rest.find(chr(34))
                        value = rest[:end] if end != -1 else ""
                    value = value.strip()
                    if value and "NOT-REAL" not in value and "SYNTHETIC" not in value and "TEST-ONLY" not in value:
                        suspicious.append("%s: %s" % (path.replace(root, ""), value[:60]))
                    index = text.find(marker, index + 1)
    assert not suspicious, suspicious



def test_lingxing_client_reads_every_setting_from_environment():
    """base_url / app_key / app_secret / access_token 全部来自环境变量。"""
    env = {
        ENV_BASE_URL: "https://lingxing.example.invalid/",
        ENV_APP_KEY: "APP-SYNTHETIC-NOT-REAL",
        ENV_APP_SECRET: "SECRET-SYNTHETIC-NOT-REAL",
        ENV_ACCESS_TOKEN: "TOKEN-SYNTHETIC-NOT-REAL",
    }
    config = LingxingConfig.from_env(env)
    assert config.base_url == "https://lingxing.example.invalid"
    assert config.app_key == "APP-SYNTHETIC-NOT-REAL"
    assert config.app_secret == "SECRET-SYNTHETIC-NOT-REAL"
    assert config.access_token == "TOKEN-SYNTHETIC-NOT-REAL"


def test_lingxing_client_requires_base_url():
    try:
        LingxingConfig.from_env({})
    except LingxingConfigError as exc:
        assert ENV_BASE_URL in str(exc)
    else:
        raise AssertionError("缺 BASE_URL 必须报错")
def test_lingxing_fetch_all_paginates_over_real_http_requests():
    """分页是真的按 offset 发请求，每页一条请求。"""
    first = [{"id": 1, "sku": "SKU-1"}]
    second = [{"id": 2, "sku": "SKU-2"}]
    http = RecordingTransport([_lingxing_page(first, total=2), _lingxing_page(second, total=2)])
    client = _client(http, page_size=1)
    items = client.fetch_all("products")
    assert [item["sku"] for item in items] == ["SKU-1", "SKU-2"]
    assert len(http.requests) == 2
    first_request = http.requests[0]
    assert first_request.method == "GET"
    assert first_request.url.startswith("https://lingxing.example.invalid/openapi/v1/products?")
    assert "offset=0" in first_request.url
    assert "offset=1" in http.requests[1].url
    assert first_request.headers["app-key"] == "APP-SYNTHETIC-NOT-REAL"
    assert first_request.describe()["headers"]["access-token"] == "***"
    assert first_request.describe()["headers"]["app-key"] == "***"
    assert first_request.describe()["headers"]["sign"] == "***"


def test_lingxing_client_retries_rate_limit_then_succeeds():
    """429 会退避重试，重试记录留痕。"""
    http = RecordingTransport([RateLimitError("429", retry_after=0.0), _lingxing_page([{"id": 1}], total=1)])
    waits = []
    transport = LingxingTransport(
        base_url="https://lingxing.example.invalid",
        app_key="APP-SYNTHETIC-NOT-REAL",
        app_secret="SECRET-SYNTHETIC-NOT-REAL",
        access_token="TOKEN-SYNTHETIC-NOT-REAL",
        http=http,
    )
    client = LingxingApiClient(
        LingxingConfig(
            base_url="https://lingxing.example.invalid",
            app_key="APP-SYNTHETIC-NOT-REAL",
            app_secret="SECRET-SYNTHETIC-NOT-REAL",
            access_token="TOKEN-SYNTHETIC-NOT-REAL",
        ),
        transport=transport,
        sleep=waits.append,
    )
    items = client.fetch_all("products")
    assert len(items) == 1
    assert len(http.requests) == 2
    assert client.retries and client.retries[0]["resource"] == "products"


def test_dingtalk_client_writes_a_row_over_http():
    body = json.dumps({"errcode": 0, "errmsg": "ok", "rowId": "ROW-SYNTHETIC-0001"}).encode("utf-8")
    transport = RecordingTransport([HttpResponse(200, {"Content-Type": "application/json"}, body)])
    client = DingTalkClient(
        DingTalkConfig(
            base_url="https://dingtalk.example.invalid",
            app_key="APP-SYNTHETIC-NOT-REAL",
            app_secret="SECRET-SYNTHETIC-NOT-REAL",
            sheet_id="SHEET-SYNTHETIC-NOT-REAL",
        ),
        http=transport,
        sleep=lambda seconds: None,
    )
    client._token = "TOKEN-SYNTHETIC-NOT-REAL"
    answer = client.write_row("B2026-0001", {"批次号": "B2026-0001", "SKU": "SKU-1001"})
    assert answer["rowId"] == "ROW-SYNTHETIC-0001"
    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.method == "POST"
    assert request.url == "https://dingtalk.example.invalid/v1.0/doc/sheets/SHEET-SYNTHETIC-NOT-REAL/rows"
    assert request.headers["x-acs-dingtalk-access-token"] == "TOKEN-SYNTHETIC-NOT-REAL"
    assert request.describe()["headers"]["x-acs-dingtalk-access-token"] == "***"
    payload = request.body_json()
    assert payload["row_key"] == "B2026-0001"
    assert payload["values"]["SKU"] == "SKU-1001"


def test_dingtalk_client_requires_credentials():
    try:
        DingTalkConfig.from_env({})
    except DingTalkConfigError as exc:
        assert "DINGTALK_BASE_URL" in str(exc)
    else:
        raise AssertionError("缺 BASE_URL 必须报错")


