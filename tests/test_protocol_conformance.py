"""协议一致性测试：把官方文档里的规则一条一条钉成断言。

为什么单独一个文件：上一轮被驳回的核心之一是「客户端看起来能发请求，但协议不是官方的」。
这里不测业务逻辑，只测**报文形态**与**算法向量**，每条断言都写明官方出处的编号。

出处：
- 领星接入指南 https://apidoc.lingxing.com/new/guides/new-instructions/
  · 2.2 公共请求参数：app_key / timestamp / sign 走 Query，access_token 走 Authorization: Bearer
  · 2.3.2 POST 业务接口：业务参数放 JSON 体，但**仍须参与签名**
  · 4.1 签名四步：ASCII 升序 → k=v&k=v → MD5(32 位转大写) → AES/ECB/PKCS5(密钥=appId) → Base64 → URL 编码
  · 4.1 注意 2：timestamp 参与签名，sign 有效期 2 分钟，不许缓存
  · 5 限流：令牌桶，被限流返回 3001008
- 领星取令牌 https://apidoc.lingxing.com/new/api-docs/Authorization/GetToken/
- 钉钉读/写区域
  https://open.dingtalk.com/document/orgapp/read-cell-properties
  https://open.dingtalk.com/document/development/update-cell-properties
"""

from __future__ import annotations

import base64
import json
from urllib.parse import parse_qsl, urlsplit

from core.dingtalk_client import (
    DEFAULT_RANGE_PATH,
    DEFAULT_SHEETS_PATH,
    DEFAULT_BASE_URL as DINGTALK_BASE_URL,
    DingTalkClient,
    DingTalkConfig,
)
from core.http_client import HttpResponse, RecordingTransport
from core.lingxing_client import (
    DEFAULT_BASE_URL as LINGXING_BASE_URL,
    DEFAULT_REFRESH_PATH,
    DEFAULT_TOKEN_PATH,
    RESOURCE_SPECS,
    LingxingApiClient,
    LingxingConfig,
)
from core.lingxing_crypto import (
    SBOX,
    aes_ecb_pkcs5_encrypt,
    aes_encrypt_block,
    canonical_string,
    generate_sign,
    md5_upper,
)

# ---------------------------------------------------------------- AES 向量

def test_aes_known_answer_vectors_from_fips_197():
    """FIPS-197 附录 C 的官方测试向量：算错一位都过不去。

    这三条向量锁住的是「我们手写的 AES 与标准实现逐字节一致」，
    也就是签名第 3 步（AES/ECB/PKCS5，密钥=appId）不会悄悄算错。
    """
    # C.1 AES-128
    assert aes_encrypt_block(
        bytes.fromhex("000102030405060708090a0b0c0d0e0f"),
        bytes.fromhex("00112233445566778899aabbccddeeff"),
    ).hex() == "69c4e0d86a7b0430d8cdb78070b4c55a"
    # C.2 AES-192
    assert aes_encrypt_block(
        bytes.fromhex("000102030405060708090a0b0c0d0e0f1011121314151617"),
        bytes.fromhex("00112233445566778899aabbccddeeff"),
    ).hex() == "dda97ca4864cdfe06eaf70a0ec0d7191"
    # C.3 AES-256
    assert aes_encrypt_block(
        bytes.fromhex("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"),
        bytes.fromhex("00112233445566778899aabbccddeeff"),
    ).hex() == "8ea2b7ca516745bfeafc49904b496089"


def test_aes_sbox_matches_published_values():
    """S-box 是程序化生成的（GF(2^8) 逆元 + 仿射变换），取几个公认值校验生成逻辑。"""
    assert SBOX[0x00] == 0x63
    assert SBOX[0x01] == 0x7C
    assert SBOX[0x53] == 0xED
    assert SBOX[0xFF] == 0x16
    assert len(SBOX) == 256
    assert len(set(SBOX)) == 256  # 双射


def test_pkcs5_padding_is_added_even_on_block_boundary():
    """PKCS5：正好整块时也要再补一整块（16 个 0x10），否则平台解不出来。"""
    digest = md5_upper("abcdef")  # 32 个字符，正好两块
    cipher = aes_ecb_pkcs5_encrypt("0123456789abcdef", digest)
    assert len(cipher) == 48  # 32 字节明文 + 整块 16 字节填充


# ------------------------------------------------------- 规范化与 MD5

def test_md5_is_32_char_upper_hex():
    """第 2 步：MD5 取 32 位十六进制并转大写（小写会被平台判签名错）。"""
    assert md5_upper("abc") == "900150983CD24FB0D6963F7D28E17F72"
    assert len(md5_upper("anything")) == 32
    assert md5_upper("anything") == md5_upper("anything").upper()


def test_canonical_string_sorts_ascii_and_skips_empty_values():
    """第 1 步：ASCII 升序；value 为空串不参与；value 为 null 参与（写成 null）。"""
    params = {"b": 1, "a": "", "c": None, "A": "x", "ab": "y"}
    assert canonical_string(params) == "A=x&ab=y&b=1&c=null"


def test_canonical_string_excludes_sign_and_authorization():
    """sign 自身与 Authorization 头不参与签名（否则自己签自己，永远对不上）。"""
    params = {"sign": "SHOULD-NOT-APPEAR", "authorization": "Bearer x", "k": "v"}
    assert canonical_string(params) == "k=v"


def test_canonical_string_serialises_nested_values_compactly():
    """dict / list 按官方 SDK 的做法序列化：键排序、无多余空格。"""
    assert canonical_string({"a": {"y": 2, "x": 1}}) == 'a={"x":1,"y":2}'
    assert canonical_string({"a": [1, 2]}) == "a=[1,2]"
    assert canonical_string({"a": True, "b": False}) == "a=true&b=false"


def test_generate_sign_matches_the_documented_four_steps():
    """完整签名 = Base64(AES-ECB-PKCS5(appId, MD5_大写(规范化串)))，逐步骤都能复算。

    这条是「签名算法与文档逐步对齐」的锁：把中间结果与最终结果都钉死，
    以后任何一步被改动（少 MD5、少 Base64、密钥换成 appSecret）都会红。
    """
    app_id = "a1b2c3d4e5f6g7h8"  # 16 字节，与真实 appId 同长度
    params = {
        "app_key": app_id,
        "access_token": "TOKEN-EXAMPLE",
        "timestamp": "1700000000",
        "offset": 0,
        "length": 1000,
        "sku_list": "",
        "note": None,
    }
    canonical = canonical_string(params)
    assert canonical == (
        "access_token=TOKEN-EXAMPLE&app_key=a1b2c3d4e5f6g7h8&length=1000&note=null&offset=0&timestamp=1700000000"
    )
    digest = md5_upper(canonical)
    assert digest == "56C16AD7E463588D22A590C9C60E6BE6"
    # 手工复算：MD5 串 -> AES -> Base64
    manual = base64.b64encode(aes_ecb_pkcs5_encrypt(app_id, digest)).decode("ascii")
    assert manual == "uldGspcnmu7rw4gt3xFsRxbqcsJHrm47b4YqNW3PWVaN31rlGdxaQOlF2bh6BsJt"
    assert generate_sign(params, app_id) == manual


def test_generate_sign_rejects_an_app_id_that_cannot_be_an_aes_key():
    """appId 长度不是 16/24/32 字节时明确报错，绝不静默补位算出平台不认的签名。"""
    try:
        generate_sign({"a": "b"}, "short")
    except ValueError as exc:
        assert "16/24/32" in str(exc)
    else:
        raise AssertionError("非法密钥长度必须报错")


# ------------------------------------------------- 领星报文形态（公共参数）

def _lingxing_page(items, total=None):
    """业务接口的统一返回形状；total 是平台给的总数，翻页靠它判断停不停。"""
    return HttpResponse(
        200,
        {"Content-Type": "application/json"},
        json.dumps({
            "code": 0,
            "message": "success",
            "total": len(items) if total is None else total,
            "data": items,
        }).encode("utf-8"),
    )


def _client(http, **overrides):
    settings = {
        "base_url": "https://lingxing.example.invalid",
        "app_id": "a1b2c3d4e5f6g7h8",
        "app_secret": "SECRET-SYNTHETIC-NOT-REAL",
        "access_token": "TOKEN-SYNTHETIC-NOT-REAL",
        "page_size": 10,
    }
    settings.update(overrides)
    return LingxingApiClient(LingxingConfig(**settings), http=http, sleep=lambda seconds: None, clock=lambda: 1700000000.0)


def test_official_hosts_and_paths_are_the_defaults():
    """默认地址与路径就是官方那几条，不在代码里散落硬编码字符串。"""
    assert LINGXING_BASE_URL == "https://openapi.lingxing.com"
    assert DEFAULT_TOKEN_PATH == "/api/auth-server/oauth/access-token"
    assert DEFAULT_REFRESH_PATH == "/api/auth-server/oauth/refresh"
    assert RESOURCE_SPECS["products"].path == "/erp/sc/routing/data/local_inventory/productList"
    assert RESOURCE_SPECS["suppliers"].path == "/erp/sc/data/local_inventory/supplier"
    assert RESOURCE_SPECS["shipments"].path == "/erp/sc/data/fba_report/shipmentList"
    assert DINGTALK_BASE_URL == "https://api.dingtalk.com"
    assert DEFAULT_TOKEN_PATH == "/api/auth-server/oauth/access-token"
    assert DEFAULT_SHEETS_PATH == "/v1.0/doc/workbooks/{workbook_id}/sheets"
    assert DEFAULT_RANGE_PATH == "/v1.0/doc/workbooks/{workbook_id}/sheets/{sheet_id}/ranges/{range_address}"


def test_public_parameters_go_to_query_and_token_goes_to_header():
    """2.2 节：app_key / timestamp / sign 走 Query；access_token 走 Authorization: Bearer。"""
    http = RecordingTransport([_lingxing_page([{"id": 1, "sku": "SKU-1"}])])
    client = _client(http)
    client.fetch_all("products")
    request = http.requests[0]
    query = dict(parse_qsl(urlsplit(request.url).query, keep_blank_values=True))
    assert set(query) == {"app_key", "timestamp", "sign"}
    assert request.headers["Authorization"] == "Bearer TOKEN-SYNTHETIC-NOT-REAL"
    assert request.headers["Content-Type"] == "application/json"


def test_signature_covers_request_body_as_well_as_public_parameters():
    """2.3.2 + 4.1：JSON 体里的业务参数也必须参与签名。"""
    import copy

    http = RecordingTransport([_lingxing_page([{"id": 1}])])
    client = _client(http)
    client.fetch_all("products")
    request = http.requests[0]
    body = request.body_json()
    query = dict(parse_qsl(urlsplit(request.url).query, keep_blank_values=True))
    sign_source = copy.deepcopy(body)
    sign_source.update({"app_key": client.config.app_id, "access_token": "TOKEN-SYNTHETIC-NOT-REAL", "timestamp": query["timestamp"]})
    assert query["sign"] == generate_sign(sign_source, client.config.app_id)


def test_pagination_parameters_follow_the_official_field_names():
    """分页字段是 offset / length（不是 page / page_size），length 有官方上限。"""
    http = RecordingTransport([
        _lingxing_page([{"id": 1, "sku": "A"}], total=2),
        _lingxing_page([{"id": 2, "sku": "B"}], total=2),
    ])
    client = _client(http, page_size=1)
    items = client.fetch_all("products")
    assert [item["sku"] for item in items] == ["A", "B"]
    first_body = http.requests[0].body_json()
    second_body = http.requests[1].body_json()
    assert first_body["offset"] == 0 and first_body["length"] == 1
    assert second_body["offset"] == 1 and second_body["length"] == 1
    assert RESOURCE_SPECS["products"].max_page_size == 1000


def test_signature_is_regenerated_for_every_request():
    """4.1 注意 2：sign 有效期 2 分钟，不许缓存——每次请求的 timestamp 与 sign 都要重算。"""
    http = RecordingTransport([
        _lingxing_page([{"id": 1, "sku": "A"}], total=2),
        _lingxing_page([{"id": 2, "sku": "B"}], total=2),
        _lingxing_page([{"id": 1, "sku": "A"}], total=2),
        _lingxing_page([{"id": 2, "sku": "B"}], total=2),
    ])
    clock = {"value": 1700000000.0}
    client = LingxingApiClient(
        LingxingConfig(
            base_url="https://lingxing.example.invalid",
            app_id="a1b2c3d4e5f6g7h8",
            app_secret="SECRET-SYNTHETIC-NOT-REAL",
            access_token="TOKEN-SYNTHETIC-NOT-REAL",
            page_size=1,
        ),
        http=http,
        sleep=lambda seconds: None,
        clock=lambda: clock["value"],
    )
    client.fetch_all("products")
    clock["value"] += 30.0  # 仍在令牌有效期内，避免顺带测令牌换发
    client.fetch_all("products")
    stamps = [dict(parse_qsl(urlsplit(request.url).query))["timestamp"] for request in http.requests]
    signs = [dict(parse_qsl(urlsplit(request.url).query))["sign"] for request in http.requests]
    assert stamps[0] != stamps[-1]
    assert signs[0] != signs[-1]


def test_token_exchange_uses_multipart_form_data():
    """取令牌接口要 multipart/form-data，字段名是 appId / appSecret（大小写敏感）。"""
    http = RecordingTransport([
        HttpResponse(200, {"Content-Type": "application/json"},
                     json.dumps({"code": "200", "msg": "OK",
                                 "data": {"access_token": "TOKEN-SYNTHETIC-NOT-REAL", "expires_in": 7199}}).encode("utf-8")),
        _lingxing_page([{"id": 1}]),
    ])
    client = _client(http, access_token="")
    client.fetch_all("products")
    token_request = http.requests[0]
    assert urlsplit(token_request.url).path == DEFAULT_TOKEN_PATH
    assert token_request.headers["Content-Type"].startswith("multipart/form-data; boundary=")
    text = token_request.body.decode("utf-8")
    assert 'name="appId"' in text
    assert 'name="appSecret"' in text
    # 令牌响应里的 code 是字符串 "200"，不是整数 200
    assert client.token_events[0]["code"] == "200"


def test_rate_limit_code_3001008_is_retried():
    """5 节：限流错误码 3001008 必须退避重试，不能当成业务失败。"""
    http = RecordingTransport([
        HttpResponse(200, {"Content-Type": "application/json"}, json.dumps({"code": 3001008, "message": "limit"}).encode("utf-8")),
        _lingxing_page([{"id": 1}]),
    ])
    client = _client(http)
    assert len(client.fetch_all("products")) == 1
    assert client.retries and client.retries[0]["reason"] == "rate_limit"


# ------------------------------------------------------ 钉钉报文形态

def test_dingtalk_range_write_puts_operator_id_in_query():
    """写区域：PUT 同一路径，operatorId 在 Query（官方文档明确要求）。"""
    http = RecordingTransport([HttpResponse(200, {"Content-Type": "application/json"},
                                            json.dumps({"values": [["x"]]}).encode("utf-8"))])
    client = DingTalkClient(
        DingTalkConfig(
            base_url="https://dingtalk.example.invalid",
            app_key="APP-SYNTHETIC-NOT-REAL",
            app_secret="SECRET-SYNTHETIC-NOT-REAL",
            workbook_id="BOOK-SYNTHETIC-NOT-REAL",
            sheet_id="SHEET-SYNTHETIC-NOT-REAL",
            operator_id="OPERATOR-SYNTHETIC-NOT-REAL",
        ),
        http=http,
        sleep=lambda seconds: None,
    )
    client._token = "TOKEN-SYNTHETIC-NOT-REAL"
    client._token_expire_at = 9_999_999_999.0
    client.write_range("A1:W2", [["x"]])
    request = http.requests[0]
    assert request.method == "PUT"
    parts = urlsplit(request.url)
    assert parts.path == "/v1.0/doc/workbooks/BOOK-SYNTHETIC-NOT-REAL/sheets/SHEET-SYNTHETIC-NOT-REAL/ranges/A1%3AW2"
    assert dict(parse_qsl(parts.query))["operatorId"] == "OPERATOR-SYNTHETIC-NOT-REAL"
    assert request.body_json() == {"values": [["x"]]}
    assert request.headers["x-acs-dingtalk-access-token"] == "TOKEN-SYNTHETIC-NOT-REAL"


def test_dingtalk_read_returns_values_matrix():
    """读区域返回 {"values": [[...]]}，客户端把它变成二维数组。"""
    payload = {"values": [["批次号", "SKU"], ["B2026-0001", "SKU-1001"]]}
    http = RecordingTransport([HttpResponse(200, {"Content-Type": "application/json"},
                                            json.dumps(payload, ensure_ascii=False).encode("utf-8"))])
    client = DingTalkClient(
        DingTalkConfig(
            base_url="https://dingtalk.example.invalid",
            app_key="APP-SYNTHETIC-NOT-REAL",
            app_secret="SECRET-SYNTHETIC-NOT-REAL",
            workbook_id="BOOK-SYNTHETIC-NOT-REAL",
            sheet_id="SHEET-SYNTHETIC-NOT-REAL",
        ),
        http=http,
        sleep=lambda seconds: None,
    )
    client._token = "TOKEN-SYNTHETIC-NOT-REAL"
    client._token_expire_at = 9_999_999_999.0
    table = client.read_range("A1:B2")
    assert table == [["批次号", "SKU"], ["B2026-0001", "SKU-1001"]]
    assert http.requests[0].method == "GET"


def test_dingtalk_token_request_shape():
    """钉钉换令牌：POST /v1.0/oauth2/accessToken，body {appKey, appSecret}，读 accessToken + expireIn。"""
    http = RecordingTransport([
        HttpResponse(200, {"Content-Type": "application/json"},
                     json.dumps({"expireIn": 7200, "accessToken": "TOKEN-SYNTHETIC-NOT-REAL"}).encode("utf-8")),
    ])
    client = DingTalkClient(
        DingTalkConfig(
            base_url="https://dingtalk.example.invalid",
            app_key="APP-SYNTHETIC-NOT-REAL",
            app_secret="SECRET-SYNTHETIC-NOT-REAL",
        ),
        http=http,
        sleep=lambda seconds: None,
    )
    assert client.access_token() == "TOKEN-SYNTHETIC-NOT-REAL"
    request = http.requests[0]
    assert request.method == "POST"
    assert urlsplit(request.url).path == "/v1.0/oauth2/accessToken"
    assert request.body_json() == {"appKey": "APP-SYNTHETIC-NOT-REAL", "appSecret": "SECRET-SYNTHETIC-NOT-REAL"}
    # 第二次不该再换（缓存 7200 秒）
    client.access_token()
    assert len(http.requests) == 1