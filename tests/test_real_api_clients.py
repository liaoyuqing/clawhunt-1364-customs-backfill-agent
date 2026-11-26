"""真实领星 / 钉钉客户端必须真的发起 HTTP 调用（买方第 1 次退货的第 1 条）。

证据落在「请求被发出并被解析」这一层：URL、方法、签名与鉴权头（值脱敏）、body 结构。
本轮换成官方 v2 协议后，这里的断言也跟着换：路径、公共参数位置、签名算法、令牌换发报文
全部按官方文档重写，并且逐条写明文档出处。

协议出处：
- 领星接入指南 https://apidoc.lingxing.com/new/guides/new-instructions/
- 领星取令牌 https://apidoc.lingxing.com/new/api-docs/Authorization/GetToken/
- 钉钉读区域 https://open.dingtalk.com/document/orgapp/read-cell-properties
- 钉钉写区域 https://open.dingtalk.com/document/development/update-cell-properties
"""

from __future__ import annotations

import json
import os
from urllib.parse import parse_qsl, urlsplit

from core.dingtalk_client import (
    ENV_APP_KEY,
    ENV_APP_KEY_ALIAS,
    ENV_APP_SECRET as DINGTALK_ENV_APP_SECRET,
    ENV_APP_SECRET_ALIAS as DINGTALK_ENV_APP_SECRET_ALIAS,
    ENV_SHEET_ID,
    ENV_WORKBOOK_ID,
    DingTalkApiError,
    DingTalkClient,
    DingTalkConfig,
    DingTalkConfigError,
    column_letter,
    range_address,
)
from core.http_client import HttpResponse, RateLimitError, RecordingTransport
from core.lingxing_client import (
    ENV_ACCESS_TOKEN,
    ENV_APP_ID,
    ENV_APP_SECRET as LINGXING_ENV_APP_SECRET,
    ENV_BASE_URL,
    LingxingApiClient,
    LingxingConfig,
    LingxingConfigError,
)

APP_ID = "APP-SYNTHETIC-NO"  # 16 字节：AES 密钥长度必须是 16/24/32
APP_ID_LONG = "APP-SYNTHETIC-NOT-REAL"
APP_SECRET = "SECRET-SYNTHETIC-NOT-REAL"
TOKEN = "TOKEN-SYNTHETIC-NOT-REAL"


def _json_response(payload, status=200):
    return HttpResponse(status, {"Content-Type": "application/json"}, json.dumps(payload).encode("utf-8"))


def _lingxing_page(items, total=None):
    """领星业务接口的统一返回形状：{code, message, data:[...], total}。"""
    return _json_response({
        "code": 0,
        "message": "success",
        "error_details": [],
        "total": total if total is not None else len(items),
        "data": items,
    })


def _lingxing_client(http, **overrides):
    settings = {
        "base_url": "https://lingxing.example.invalid",
        "app_id": APP_ID,
        "app_secret": APP_SECRET,
        "access_token": TOKEN,
        "page_size": 100,
    }
    settings.update(overrides)
    return LingxingApiClient(LingxingConfig(**settings), http=http, sleep=lambda seconds: None, clock=lambda: 1700000000.0)


def _query_of(url):
    return dict(parse_qsl(urlsplit(url).query, keep_blank_values=True))


def _authorize(client, token=TOKEN):
    """跳过令牌换发：直接把 token 与过期时间都设上，让业务请求马上可发。

    只设 _token 不够：过期时间是 0，客户端会认为令牌已过期再换一次。
    """
    client._token = token
    client._token_expire_at = 9_999_999_999.0
    return client


def test_repository_contains_no_real_looking_credentials():
    """仓库里不能出现像真实凭据的字面量。

    只看「键 = 字面量」这一种形式：JSON/YAML 的 "app_secret": "xxx" 与 Python 的
    app_secret="xxx"。取值必须为空串或明显的示意形态。
    tests/ 目录不参与扫描：测试代码里必然出现键名与示意值本身。
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    markers = [
        "\"app_secret\":", "\"appSecret\":", "\"app_key\":", "\"appKey\":",
        "\"app_id\":", "\"appId\":",
        "\"access_token\":", "\"accessToken\":", "\"token\":",
        "app_secret=", "appSecret=", "app_key=", "appKey=",
        "app_id=", "appId=",
        "access_token=", "accessToken=", "token=",
    ]
    suspicious = []
    for folder, _dirs, files in os.walk(root):
        parts = folder.replace(root, "").split(os.sep)
        if ".venv" in parts or ".git" in parts or "__pycache__" in parts or "tests" in parts:
            continue
        for name in files:
            if not name.endswith((".py", ".json", ".yaml", ".yml", ".md", ".txt", ".sh", ".ini")):
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
    """base_url / appId / appSecret / accessToken 全部来自环境变量。"""
    env = {
        ENV_BASE_URL: "https://lingxing.example.invalid/",
        ENV_APP_ID: APP_ID,
        LINGXING_ENV_APP_SECRET: APP_SECRET,
        ENV_ACCESS_TOKEN: TOKEN,
    }
    config = LingxingConfig.from_env(env)
    assert config.base_url == "https://lingxing.example.invalid"
    assert config.app_id == APP_ID
    assert config.app_secret == APP_SECRET
    assert config.access_token == TOKEN


def test_lingxing_client_accepts_the_legacy_app_key_name():
    """上一轮用的是 LINGXING_APP_KEY；客户侧可能已经配了这个名字，改名不能把人卡死。"""
    config = LingxingConfig.from_env({ENV_APP_ID: "", "LINGXING_APP_KEY": APP_ID, LINGXING_ENV_APP_SECRET: APP_SECRET})
    assert config.app_id == APP_ID


def test_lingxing_client_requires_credentials_and_names_them():
    """缺凭据必须报错，并且明确说是哪个变量（不再要求 BASE_URL：官方地址有默认值）。"""
    try:
        LingxingConfig.from_env({}).require_credentials()
    except LingxingConfigError as exc:
        assert ENV_APP_ID in str(exc)
        assert LINGXING_ENV_APP_SECRET in str(exc)
    else:
        raise AssertionError("缺凭据必须报错")


def test_lingxing_fetch_all_paginates_over_real_http_requests():
    """分页是真的按 offset 发请求，每页一条；路径与公共参数位置按官方 v2 协议。"""
    first = [{"id": 1, "sku": "SKU-1"}]
    second = [{"id": 2, "sku": "SKU-2"}]
    http = RecordingTransport([_lingxing_page(first, total=2), _lingxing_page(second, total=2)])
    client = _lingxing_client(http, page_size=1)
    items = client.fetch_all("products")
    assert [item["sku"] for item in items] == ["SKU-1", "SKU-2"]
    assert len(http.requests) == 2

    first_request = http.requests[0]
    parts = urlsplit(first_request.url)
    assert first_request.method == "POST"
    assert parts.path == "/erp/sc/routing/data/local_inventory/productList"
    query = _query_of(first_request.url)
    # 三个公共参数走 Query：app_key / timestamp / sign；access_token 走 Authorization 头。
    assert query["app_key"] == APP_ID
    assert query["timestamp"] == "1700000000"
    assert query["sign"]
    assert "access_token" not in query
    assert first_request.headers["Authorization"] == "Bearer %s" % TOKEN
    # 业务参数在 body 里，并且仍参与签名（官方 2.3.2）
    body = first_request.body_json()
    assert body["offset"] == 0
    assert body["length"] == 1
    # 文件里不得出现凭据明文
    described = first_request.describe()["headers"]
    assert described["Authorization"] == "***"
    assert query["sign"] != ""


def test_lingxing_signature_covers_body_and_public_parameters():
    """签名覆盖业务参数 + app_key + access_token + timestamp（官方 4.1）。"""
    from core.lingxing_crypto import generate_sign

    http = RecordingTransport([_lingxing_page([{"id": 1}], total=1)])
    client = _lingxing_client(http, page_size=1)
    client.fetch_all("products")
    request = http.requests[0]
    query = _query_of(request.url)
    body = request.body_json()
    expected = dict(body)
    expected["app_key"] = APP_ID
    expected["access_token"] = TOKEN
    expected["timestamp"] = query["timestamp"]
    assert query["sign"] == generate_sign(expected, APP_ID)


def test_lingxing_token_is_exchanged_with_multipart_when_absent():
    """没有 token 时先用 appId/appSecret 换发；报文形态是 multipart/form-data。"""
    http = RecordingTransport([
        _json_response({"code": "200", "msg": "OK", "data": {"access_token": TOKEN, "refresh_token": "REFRESH-SYNTHETIC", "expires_in": 7199}}),
        _lingxing_page([{"id": 1}], total=1),
    ])
    client = _lingxing_client(http, access_token="")
    client.fetch_all("products")
    token_request = http.requests[0]
    assert token_request.method == "POST"
    assert urlsplit(token_request.url).path == "/api/auth-server/oauth/access-token"
    assert token_request.headers["Content-Type"].startswith("multipart/form-data; boundary=")
    body = token_request.body.decode("utf-8")
    assert "name=\"appId\"" in body and APP_ID in body
    assert "name=\"appSecret\"" in body and APP_SECRET in body
    assert client.token_events and client.token_events[0]["action"] == "access-token"
    assert http.requests[1].headers["Authorization"] == "Bearer %s" % TOKEN


def test_lingxing_client_retries_rate_limit_then_succeeds():
    """HTTP 429 与业务码 3001008 都会退避重试，重试记录留痕。"""
    http = RecordingTransport([
        RateLimitError("429", retry_after=0.0),
        _json_response({"code": 3001008, "message": "too many requests"}),
        _lingxing_page([{"id": 1}], total=1),
    ])
    client = _lingxing_client(http)
    items = client.fetch_all("products")
    assert len(items) == 1
    assert len(http.requests) == 3
    reasons = [item["reason"] for item in client.retries]
    assert reasons[0] == "http429"
    assert reasons[1] == "rate_limit"


def test_lingxing_business_error_is_raised_not_swallowed():
    """非 0 业务码必须抛异常并带上 code 与 payload，不能当成成功。"""
    from core.lingxing_client import LingxingApiError

    http = RecordingTransport([_json_response({"code": 2001001, "msg": "app not exist", "data": None})])
    client = _lingxing_client(http)
    try:
        client.fetch_all("products")
    except LingxingApiError as exc:
        assert str(exc.code) == "2001001"
        assert exc.payload.get("msg") == "app not exist"
    else:
        raise AssertionError("业务错误码必须抛异常")


def test_dingtalk_range_paths_and_headers():
    """钉钉读写都走官方 workbooks/sheets/ranges 路径，鉴权头是 x-acs-dingtalk-access-token。"""
    transport = RecordingTransport([
        _json_response({"sheets": [{"id": "SHEET-SYNTHETIC-NOT-REAL", "name": "台账"}]}),
    ])
    client = DingTalkClient(
        DingTalkConfig(
            base_url="https://dingtalk.example.invalid",
            app_key="APP-SYNTHETIC-NOT-REAL",
            app_secret="SECRET-SYNTHETIC-NOT-REAL",
            workbook_id="BOOK-SYNTHETIC-NOT-REAL",
        ),
        http=transport,
        sleep=lambda seconds: None,
    )
    _authorize(client)
    sheets = client.list_sheets()
    assert sheets and sheets[0]["id"] == "SHEET-SYNTHETIC-NOT-REAL"
    request = transport.requests[0]
    assert request.method == "GET"
    assert request.url == "https://dingtalk.example.invalid/v1.0/doc/workbooks/BOOK-SYNTHETIC-NOT-REAL/sheets"
    assert request.headers["x-acs-dingtalk-access-token"] == TOKEN
    assert request.describe()["headers"]["x-acs-dingtalk-access-token"] == "***"


def test_dingtalk_write_row_uses_put_with_operator_id():
    """写一行：先读表头、再读现有行定位行号，最后 PUT 单行区域，query 必带 operatorId。"""
    header = _json_response({"values": [["批次号", "SKU", "产品中文报关名"]]})
    existing = _json_response({"values": [["批次号", "SKU", "产品中文报关名"], ["B2026-0001", "SKU-1001", "旧名"]]})
    write_answer = _json_response({"values": [["B2026-0001", "SKU-1001", "产品甲"]]})
    transport = RecordingTransport([header, existing, write_answer])
    client = DingTalkClient(
        DingTalkConfig(
            base_url="https://dingtalk.example.invalid",
            app_key="APP-SYNTHETIC-NOT-REAL",
            app_secret="SECRET-SYNTHETIC-NOT-REAL",
            workbook_id="BOOK-SYNTHETIC-NOT-REAL",
            sheet_id="SHEET-SYNTHETIC-NOT-REAL",
            operator_id="OPERATOR-SYNTHETIC-NOT-REAL",
        ),
        http=transport,
        sleep=lambda seconds: None,
    )
    _authorize(client)
    answer = client.write_row("B2026-0001", {"批次号": "B2026-0001", "SKU": "SKU-1001", "产品中文报关名": "产品甲"})
    assert [request.method for request in transport.requests] == ["GET", "GET", "PUT"]
    write_request = transport.requests[2]
    parts = urlsplit(write_request.url)
    assert parts.path == "/v1.0/doc/workbooks/BOOK-SYNTHETIC-NOT-REAL/sheets/SHEET-SYNTHETIC-NOT-REAL/ranges/A2%3AC2"
    assert _query_of(write_request.url)["operatorId"] == "OPERATOR-SYNTHETIC-NOT-REAL"
    assert write_request.body_json() == {"values": [["B2026-0001", "SKU-1001", "产品甲"]]}
    assert write_request.headers["x-acs-dingtalk-access-token"] == TOKEN
    # 定位到的是已有行（第 2 行），不是追加
    assert answer["range"] == "A2:C2"
    assert answer["sheetId"] == "SHEET-SYNTHETIC-NOT-REAL"


def test_dingtalk_write_row_appends_when_row_key_is_new():
    """行键不存在时追加到末尾，行号按现有行数 +1 计算，不猜。"""
    header = _json_response({"values": [["批次号", "SKU"]]})
    existing = _json_response({"values": [["批次号", "SKU"], ["B2026-0001", "SKU-1001"]]})
    write_answer = _json_response({"values": [["B2026-0002", "SKU-1002"]]})
    transport = RecordingTransport([header, existing, write_answer])
    client = DingTalkClient(
        DingTalkConfig(
            base_url="https://dingtalk.example.invalid",
            app_key="APP-SYNTHETIC-NOT-REAL",
            app_secret="SECRET-SYNTHETIC-NOT-REAL",
            workbook_id="BOOK-SYNTHETIC-NOT-REAL",
            sheet_id="SHEET-SYNTHETIC-NOT-REAL",
            operator_id="OPERATOR-SYNTHETIC-NOT-REAL",
        ),
        http=transport,
        sleep=lambda seconds: None,
    )
    _authorize(client)
    answer = client.write_row("B2026-0002", {"批次号": "B2026-0002", "SKU": "SKU-1002"})
    assert answer["range"] == "A3:B3"


def test_dingtalk_missing_scope_is_reported_with_apply_link():
    """403 缺权限不是「重试能好」的错误：必须一次报错，并把 scope 与申请链接给出来。"""
    from core.dingtalk_client import DingTalkApiError

    # 平台用 HTTPError 抛 403：body 是平台原文，客户端要能从里面取出 scope 与申请链接。
    from core.http_client import HttpError

    denied = HttpError(
        "http 403 from https://dingtalk.example.invalid/v1.0/doc/workbooks/BOOK-SYNTHETIC-NOT-REAL/sheets",
        status=403,
        body=json.dumps({
            "code": "Forbidden.AccessDenied.AccessTokenPermissionDenied",
            "message": "应用尚未开通所需的权限：[Document.Workbook.Read]，点击链接申请并开通即可："
                       "https://open-dev.dingtalk.com/appscope/apply?content=APP%23Document.Workbook.Read",
            "accessdenieddetail": {"requiredScopes": ["Document.Workbook.Read"]},
        }, ensure_ascii=False),
    )
    transport = RecordingTransport([denied])
    client = DingTalkClient(
        DingTalkConfig(
            base_url="https://dingtalk.example.invalid",
            app_key="APP-SYNTHETIC-NOT-REAL",
            app_secret="SECRET-SYNTHETIC-NOT-REAL",
            workbook_id="BOOK-SYNTHETIC-NOT-REAL",
            sheet_id="SHEET-SYNTHETIC-NOT-REAL",
        ),
        http=transport,
        sleep=lambda seconds: None,
    )
    _authorize(client)
    try:
        client.read_range("A1:Z3")
    except DingTalkApiError as exc:
        assert exc.missing_scope == "Document.Workbook.Read"
        assert "appscope/apply" in str(exc)
    else:
        raise AssertionError("缺权限必须报错")
    # 只发了一次，没有无意义的退避重试
    assert len(transport.requests) == 1


def test_dingtalk_read_paths_carry_operator_id_when_configured():
    """配了 operatorId 时，读的两条路径也要带上它。

    实测（2026-11-26，真实调用）：权限授予后 /sheets 与 /ranges 会先校验 operatorId，
    缺了返回 400 MissingoperatorId。上一轮只给 PUT 带，读路径是漏的。
    """
    transport = RecordingTransport([
        _json_response({"sheets": [{"id": "SHEET-SYNTHETIC-NOT-REAL"}]}),
        _json_response({"values": [["批次号"]]}),
    ])
    client = _authorize(DingTalkClient(
        DingTalkConfig(
            base_url="https://dingtalk.example.invalid",
            app_key="APP-SYNTHETIC-NOT-REAL",
            app_secret="SECRET-SYNTHETIC-NOT-REAL",
            workbook_id="BOOK-SYNTHETIC-NOT-REAL",
            sheet_id="SHEET-SYNTHETIC-NOT-REAL",
            operator_id="OPERATOR-SYNTHETIC-NOT-REAL",
        ),
        http=transport,
        sleep=lambda seconds: None,
    ))
    client.list_sheets()
    client.read_range("A1:B1")
    assert _query_of(transport.requests[0].url)["operatorId"] == "OPERATOR-SYNTHETIC-NOT-REAL"
    assert _query_of(transport.requests[1].url)["operatorId"] == "OPERATOR-SYNTHETIC-NOT-REAL"
    # 路径本身不因加了 query 而变形
    assert urlsplit(transport.requests[0].url).path == "/v1.0/doc/workbooks/BOOK-SYNTHETIC-NOT-REAL/sheets"
    assert urlsplit(transport.requests[1].url).path.endswith("/sheets/SHEET-SYNTHETIC-NOT-REAL/ranges/A1%3AB1")


def test_dingtalk_missing_operator_id_is_not_retried():
    """400 MissingoperatorId 是确定性错误：不重试，并把该配哪个变量写进错误信息。

    实测原文：{"code":"MissingoperatorId","message":"operatorId is mandatory for this action."}
    另一种：{"code":"invalidRequest.inputArgs.invalid","message":"paramError-operatorId"}
    """
    from core.dingtalk_client import ENV_OPERATOR_ID
    from core.http_client import HttpError

    for body, expect_word in (
        ({"code": "MissingoperatorId", "message": "operatorId is mandatory for this action."}, "缺少 operatorId"),
        ({"code": "invalidRequest.inputArgs.invalid", "message": "paramError-operatorId"}, "unionId"),
    ):
        denied = HttpError(
            "http 400 from https://dingtalk.example.invalid/v1.0/doc/workbooks/BOOK-SYNTHETIC-NOT-REAL/sheets",
            status=400,
            body=json.dumps(body, ensure_ascii=False),
        )
        transport = RecordingTransport([denied])
        client = _authorize(DingTalkClient(
            DingTalkConfig(
                base_url="https://dingtalk.example.invalid",
                app_key="APP-SYNTHETIC-NOT-REAL",
                app_secret="SECRET-SYNTHETIC-NOT-REAL",
                workbook_id="BOOK-SYNTHETIC-NOT-REAL",
            ),
            http=transport,
            sleep=lambda seconds: None,
        ))
        try:
            client.list_sheets()
        except DingTalkApiError as exc:
            assert ENV_OPERATOR_ID in str(exc)
            assert expect_word in str(exc)
            assert exc.status == 400
        else:
            raise AssertionError("400 必须报错")
        assert len(transport.requests) == 1, body


def test_dingtalk_unknown_4xx_is_not_retried():
    """其余 4xx（路径写错等）也不该退避重试：重试只会把同一个错误再发三次。"""
    from core.http_client import HttpError

    denied = HttpError(
        "http 404 from https://dingtalk.example.invalid/v1.0/doc/nope",
        status=404,
        body=json.dumps({"code": "InvalidAction.NotFound", "message": "Specified api is not found"}, ensure_ascii=False),
    )
    transport = RecordingTransport([denied])
    client = _authorize(DingTalkClient(
        DingTalkConfig(
            base_url="https://dingtalk.example.invalid",
            app_key="APP-SYNTHETIC-NOT-REAL",
            app_secret="SECRET-SYNTHETIC-NOT-REAL",
            workbook_id="BOOK-SYNTHETIC-NOT-REAL",
        ),
        http=transport,
        sleep=lambda seconds: None,
    ))
    try:
        client.list_sheets()
    except DingTalkApiError as exc:
        assert exc.status == 404
    else:
        raise AssertionError("404 必须报错")
    assert len(transport.requests) == 1


def test_dingtalk_client_requires_credentials():
    """缺凭据时错误信息里点名要哪个变量（含兼容别名）。"""
    try:
        DingTalkConfig.from_env({}).require_credentials()
    except DingTalkConfigError as exc:
        assert ENV_APP_KEY in str(exc) and ENV_APP_KEY_ALIAS in str(exc)
        assert DINGTALK_ENV_APP_SECRET in str(exc) and DINGTALK_ENV_APP_SECRET_ALIAS in str(exc)
    else:
        raise AssertionError("缺凭据必须报错")


def test_dingtalk_target_requires_workbook_and_sheet():
    """读写区域必须先有 workbookId 与 sheetId，缺了就报错而不是打到错地址。"""
    try:
        DingTalkConfig.from_env({}).require_target()
    except DingTalkConfigError as exc:
        assert ENV_WORKBOOK_ID in str(exc)
        assert ENV_SHEET_ID in str(exc)
    else:
        raise AssertionError("缺目标表必须报错")


def test_column_letter_and_range_address():
    """列字母与区域地址：1 -> A，27 -> AA，A1:W2 形态。"""
    assert column_letter(1) == "A"
    assert column_letter(26) == "Z"
    assert column_letter(27) == "AA"
    assert column_letter(23) == "W"
    assert range_address(1, 1, 23) == "A1:W1"
    assert range_address(2, 2, 2) == "A2:B2"