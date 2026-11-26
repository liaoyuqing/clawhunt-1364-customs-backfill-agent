"""领星 ERP 开放平台客户端（官方 v2 协议，逐个字段对齐官方文档）。

协议依据（全部在公网可免密钥读取，注释里逐条标出处）：
- 接入指南 https://apidoc.lingxing.com/new/guides/new-instructions/
  · 2.2 公共请求参数：app_key / timestamp / sign 走 URL Query，access_token 走 Authorization: Bearer
  · 2.3.2 POST 业务接口：业务参数放 JSON 请求体，但**仍须参与签名**
  · 4.1 sign 生成规则：ASCII 升序 → k=v&k=v → MD5(32 位转大写) → AES/ECB/PKCS5(密钥=appId) → Base64 → URL 编码
  · 4.1 注意 2：timestamp 参与签名时 sign 有效期 2 分钟，必须实时生成、不许缓存 sign
  · 5 限流：令牌桶，维度 appId + 接口 url，被限流返回 3001008
- 获取令牌 https://apidoc.lingxing.com/new/api-docs/Authorization/GetToken/
  POST /api/auth-server/oauth/access-token，multipart/form-data，字段 appId / appSecret；
  成功返回 {"code":"200","msg":"OK","data":{access_token,refresh_token,expires_in}}（注意 code 是字符串）。
- 续约令牌 https://apidoc.lingxing.com/new/api-docs/Authorization/RefreshToken/
  POST /api/auth-server/oauth/refresh，multipart appId / refreshToken；refresh_token 有效期 2 小时且一次性。
- 业务接口（全部 POST + JSON body）：
  · 产品列表 /erp/sc/routing/data/local_inventory/productList
  · 供应商列表 /erp/sc/data/local_inventory/supplier
  · FBA 货件列表 /erp/sc/data/fba_report/shipmentList
  · 报关资料列表 /basicOpen/customs/search
  · 报关资料明细 /supplyShipmentBasic/openApi/customsDeclaration/batchDetail

凭据只从环境变量读，仓库里没有任何字面量凭据。传输层复用 core.http_client（urllib，无第三方依赖）。
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from .http_client import HttpError, HttpRequest, RateLimitError, Transport, UrllibTransport, json_request
from .lingxing_crypto import generate_sign

DEFAULT_BASE_URL = "https://openapi.lingxing.com"
DEFAULT_TOKEN_PATH = "/api/auth-server/oauth/access-token"
DEFAULT_REFRESH_PATH = "/api/auth-server/oauth/refresh"

ENV_BASE_URL = "LINGXING_BASE_URL"
ENV_APP_ID = "LINGXING_APP_ID"
ENV_APP_SECRET = "LINGXING_APP_SECRET"
ENV_ACCESS_TOKEN = "LINGXING_ACCESS_TOKEN"
ENV_REFRESH_TOKEN = "LINGXING_REFRESH_TOKEN"
ENV_PAGE_SIZE = "LINGXING_PAGE_SIZE"

# 旧名兼容：上一轮用的是 LINGXING_APP_KEY，客户侧可能已经配了这个名字。
ENV_APP_ID_ALIASES = (ENV_APP_ID, "LINGXING_APP_KEY")

# 限流错误码（官方指南第 5 节）
RATE_LIMIT_CODE = 3001008
# 公共参数错误码（官方指南 2.2 节）
TOKEN_MISMATCH_CODE = 3001022
INVALID_AUTHORIZATION_CODE = 3001025


class LingxingConfigError(RuntimeError):
    """必需的环境变量缺失或格式不对。"""


class LingxingApiError(RuntimeError):
    """平台返回非 0 业务码。"""

    def __init__(self, message: str, code: Any = None, payload: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.code = code
        self.payload = payload or {}


@dataclass(frozen=True)
class ResourceSpec:
    """一个业务接口的协议规格：路径、默认请求体、分页方式、列表取法。"""

    name: str
    path: str
    doc_url: str
    body: Tuple[Tuple[str, Any], ...] = ()
    page_size_key: str = "length"
    offset_key: str = "offset"
    list_path: Tuple[str, ...] = ("data",)
    total_path: Tuple[str, ...] = ("total",)
    max_page_size: int = 1000

    def default_body(self) -> Dict[str, Any]:
        return {key: value for key, value in self.body}


# 每个接口的返回值形状以官方文档的「返回结果」章节为准，list_path 就是照着文档写的。
RESOURCE_SPECS: Dict[str, ResourceSpec] = {
    "products": ResourceSpec(
        name="products",
        path="/erp/sc/routing/data/local_inventory/productList",
        doc_url="https://apidoc.lingxing.com/new/api-docs/Product/ProductLists/",
        body=(("offset", 0), ("length", 1000)),
        list_path=("data",),
        total_path=("total",),
    ),
    "suppliers": ResourceSpec(
        name="suppliers",
        path="/erp/sc/data/local_inventory/supplier",
        doc_url="https://apidoc.lingxing.com/new/api-docs/Purchase/Supplier/",
        body=(("offset", 0), ("length", 1000)),
        list_path=("data",),
        total_path=("total",),
    ),
    "shipments": ResourceSpec(
        name="shipments",
        path="/erp/sc/data/fba_report/shipmentList",
        doc_url="https://apidoc.lingxing.com/new/api-docs/FBA/FBAShipmentList/",
        body=(("sid", ""), ("start_date", ""), ("end_date", ""), ("offset", 0), ("length", 1000)),
        list_path=("data", "list"),
        total_path=("total",),
    ),
    "customs": ResourceSpec(
        name="customs",
        path="/basicOpen/customs/search",
        doc_url="https://apidoc.lingxing.com/new/api-docs/Logistics/CustomsSearch/",
        body=(("offset", 0), ("length", 20)),
        max_page_size=200,
        list_path=("data", "list"),
        total_path=("data", "total"),
    ),
    "customs_detail": ResourceSpec(
        name="customs_detail",
        path="/supplyShipmentBasic/openApi/customsDeclaration/batchDetail",
        doc_url="https://apidoc.lingxing.com/new/api-docs/Logistics/CustomsDeclarationBatchDetail/",
        body=(("declarationNos", []),),
        list_path=("data",),
        total_path=("total",),
    ),
}

PAGED_RESOURCES = tuple(name for name, spec in RESOURCE_SPECS.items() if spec.name != "customs_detail")


def _dig(payload: Any, path: Sequence[str]) -> Any:
    current = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _multipart(fields: Dict[str, str]) -> Tuple[bytes, str]:
    """构造 multipart/form-data 请求体（令牌接口强制要求该编码）。"""
    boundary = "----lingxing-%s" % uuid.uuid4().hex
    chunks: List[bytes] = []
    for key, value in fields.items():
        chunks.append(("--%s\r\n" % boundary).encode("utf-8"))
        chunks.append(("Content-Disposition: form-data; name=\"%s\"\r\n\r\n" % key).encode("utf-8"))
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")
    chunks.append(("--%s--\r\n" % boundary).encode("utf-8"))
    return b"".join(chunks), "multipart/form-data; boundary=%s" % boundary


def _with_query(url: str, params: Dict[str, Any]) -> str:
    """把参数按 URL 编码接到 url 上；sign 中的 + / = 必须转义，否则平台收到的签名会被改写。"""
    parts = urlsplit(url)
    query = [(key, value) for key, value in params.items() if value is not None]
    encoded = urlencode([(key, str(value)) for key, value in query], quote_via=quote, safe="")
    merged = parts.query + ("&" if parts.query and encoded else "") + encoded
    return urlunsplit((parts.scheme, parts.netloc, parts.path, merged, parts.fragment))


@dataclass
class LingxingConfig:
    """连接设置；每个字段都从环境变量读，仓库里不含凭据。"""

    base_url: str = DEFAULT_BASE_URL
    app_id: str = ""
    app_secret: str = ""
    access_token: str = ""
    refresh_token: str = ""
    page_size: int = 100
    max_retries: int = 4
    timeout: float = 30.0
    token_path: str = DEFAULT_TOKEN_PATH
    refresh_path: str = DEFAULT_REFRESH_PATH

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "LingxingConfig":
        source = env if env is not None else os.environ
        base_url = str(source.get(ENV_BASE_URL, "") or DEFAULT_BASE_URL).strip().rstrip("/")
        app_id = ""
        for name in ENV_APP_ID_ALIASES:
            app_id = str(source.get(name, "")).strip()
            if app_id:
                break
        raw_page = str(source.get(ENV_PAGE_SIZE, "") or 100).strip()
        try:
            page_size = int(raw_page)
        except ValueError:
            raise LingxingConfigError("%s 必须是整数：%r" % (ENV_PAGE_SIZE, raw_page))
        return cls(
            base_url=base_url,
            app_id=app_id,
            app_secret=str(source.get(ENV_APP_SECRET, "")).strip(),
            access_token=str(source.get(ENV_ACCESS_TOKEN, "")).strip(),
            refresh_token=str(source.get(ENV_REFRESH_TOKEN, "")).strip(),
            page_size=page_size,
        )

    def require_credentials(self) -> None:
        """没有 appId/appSecret 就无法换令牌，直接报清楚缺哪个变量。"""
        missing = []
        if not self.app_id:
            missing.append(ENV_APP_ID)
        if not self.app_secret:
            missing.append(ENV_APP_SECRET)
        if missing:
            raise LingxingConfigError("缺少环境变量：%s" % "、".join(missing))


@dataclass
class LingxingApiClient:
    """真实开放平台客户端：令牌换发/续约、v2 签名、分页、限流退避、错误码映射。"""

    config: LingxingConfig
    http: Transport = field(default_factory=UrllibTransport)
    sleep: Optional[Any] = None
    clock: Optional[Any] = None
    requests: List[Dict[str, Any]] = field(default_factory=list)
    retries: List[Dict[str, Any]] = field(default_factory=list)
    token_events: List[Dict[str, Any]] = field(default_factory=list)
    responses: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._sleep = self.sleep or time.sleep
        self._clock = self.clock or time.time
        self._token = ""
        self._token_expire_at = 0.0
        self._refresh_token = self.config.refresh_token

    # ---- 令牌 ----
    def _apply_token_payload(self, body: Dict[str, Any], source: str) -> str:
        data = body.get("data") or {}
        token = str(data.get("access_token", "")).strip()
        if not token:
            raise LingxingApiError("令牌接口没有返回 access_token：code=%s msg=%s" % (body.get("code"), body.get("msg")), code=body.get("code"), payload=body)
        expires = data.get("expires_in")
        try:
            ttl = float(expires)
        except (TypeError, ValueError):
            ttl = 7199.0
        self._token = token
        self._token_expire_at = float(self._clock()) + max(ttl - 60.0, 60.0)
        refresh = str(data.get("refresh_token", "")).strip()
        if refresh:
            self._refresh_token = refresh
        self.token_events.append({
            "action": source,
            "code": body.get("code"),
            "msg": body.get("msg"),
            "expires_in": expires,
            "token_length": len(token),
        })
        return token

    def _token_call(self, path: str, fields: Dict[str, str]) -> Dict[str, Any]:
        body, content_type = _multipart(fields)
        url = self.config.base_url + path
        headers = {"Content-Type": content_type, "Accept": "application/json"}
        request = HttpRequest(method="POST", url=url, headers=headers, body=body, timeout=self.config.timeout)
        self.requests.append(request.describe())
        response = self.http(request)
        self.responses.append({"url": url, "status": response.status, "body_sha256": _sha256(response.body)})
        try:
            return dict(response.json())
        except Exception as exc:
            raise HttpError("令牌接口返回非 JSON：%s" % exc, status=response.status, body=response.text())

    def access_token(self, force: bool = False) -> str:
        """取可用 token：优先复用未过期的，其次续约，最后用 appId/appSecret 换发。"""
        now = float(self._clock())
        if not force and self._token and now < self._token_expire_at:
            return self._token
        if self.config.access_token and not force and not self._token:
            self._token = self.config.access_token
            self._token_expire_at = now + 60.0
            self.token_events.append({"action": "env", "code": None, "msg": "from " + ENV_ACCESS_TOKEN, "expires_in": None, "token_length": len(self._token)})
            return self._token
        if self._refresh_token and self.config.app_id:
            body = self._token_call(self.config.refresh_path, {"appId": self.config.app_id, "refreshToken": self._refresh_token})
            if str(body.get("code")) == "200":
                return self._apply_token_payload(body, "refresh")
            raise LingxingApiError("续约令牌失败：code=%s msg=%s" % (body.get("code"), body.get("msg")), code=body.get("code"), payload=body)
        self.config.require_credentials()
        body = self._token_call(self.config.token_path, {"appId": self.config.app_id, "appSecret": self.config.app_secret})
        if str(body.get("code")) != "200":
            raise LingxingApiError("换发令牌失败：code=%s msg=%s" % (body.get("code"), body.get("msg")), code=body.get("code"), payload=body)
        return self._apply_token_payload(body, "access-token")

    def refresh(self) -> str:
        """显式续约（给运维脚本用）。"""
        if not self._refresh_token:
            raise LingxingConfigError("缺少 %s，无法续约" % ENV_REFRESH_TOKEN)
        body = self._token_call(self.config.refresh_path, {"appId": self.config.app_id, "refreshToken": self._refresh_token})
        if str(body.get("code")) != "200":
            raise LingxingApiError("续约令牌失败：code=%s msg=%s" % (body.get("code"), body.get("msg")), code=body.get("code"), payload=body)
        return self._apply_token_payload(body, "refresh")

    # ---- 业务请求 ----
    def _timestamp(self) -> str:
        """官方要求 10 位秒级时间戳，且 sign 不能缓存（有效期 2 分钟）。"""
        return str(int(self._clock()))

    def signed_request(
        self,
        spec: ResourceSpec,
        body: Optional[Dict[str, Any]] = None,
        extra_query: Optional[Dict[str, Any]] = None,
    ) -> Tuple[HttpRequest, Any]:
        """按官方 4.1/4.2 生成签名并发送：body 与 query 都参与签名，sign 走 Query。"""
        token = self.access_token()
        timestamp = self._timestamp()
        payload = dict(body if body is not None else spec.default_body())
        query_business = dict(extra_query or {})
        sign_source: Dict[str, Any] = {}
        sign_source.update(payload)
        sign_source.update(query_business)
        sign_source["app_key"] = self.config.app_id
        sign_source["access_token"] = token
        sign_source["timestamp"] = timestamp
        sign = generate_sign(sign_source, self.config.app_id)
        query = dict(query_business)
        query["app_key"] = self.config.app_id
        query["timestamp"] = timestamp
        query["sign"] = sign
        url = _with_query(self.config.base_url + spec.path, query)
        headers = {
            "Authorization": "Bearer %s" % token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        request = HttpRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=self.config.timeout,
        )
        self.requests.append(request.describe())
        response = self.http(request)
        self.responses.append({"url": spec.path, "status": response.status, "body_sha256": _sha256(response.body)})
        try:
            answer = response.json()
        except Exception as exc:
            raise HttpError("响应不是 JSON（%s）：%s" % (spec.path, exc), status=response.status, body=response.text())
        return request, answer

    def _handle_business_code(self, spec: ResourceSpec, answer: Dict[str, Any], attempt: int) -> Optional[str]:
        """把业务错误码映射成异常或重试；返回 None 表示可以重试。"""
        code = answer.get("code")
        try:
            numeric = int(code)
        except (TypeError, ValueError):
            numeric = -1
        if numeric == RATE_LIMIT_CODE:
            return "rate_limit"
        message = str(answer.get("message") or answer.get("msg") or "")
        details = answer.get("error_details")
        if numeric == TOKEN_MISMATCH_CODE:
            raise LingxingApiError("公共参数错误 3001022（头与 Query 的 token 不一致）：%s" % message, code=code, payload=answer)
        if numeric == INVALID_AUTHORIZATION_CODE:
            raise LingxingApiError("公共参数错误 3001025（Authorization 为空/格式错/重复）：%s" % message, code=code, payload=answer)
        raise LingxingApiError(
            "领星接口 %s 返回 code=%s message=%s error_details=%s" % (spec.path, code, message, details),
            code=code,
            payload=answer,
        )

    def call(self, resource: str, body: Optional[Dict[str, Any]] = None, extra_query: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """发一次请求并按官方错误码处理限流退避与重试。"""
        spec = RESOURCE_SPECS.get(resource)
        if spec is None:
            raise LingxingApiError("未知资源 %r，可用：%s" % (resource, "、".join(sorted(RESOURCE_SPECS))))
        attempt = 0
        while True:
            try:
                _request, answer = self.signed_request(spec, body=body, extra_query=extra_query)
            except RateLimitError as exc:
                if attempt >= self.config.max_retries:
                    raise
                wait = float(getattr(exc, "retry_after", 1.0) or 1.0)
                self.retries.append({"resource": resource, "attempt": attempt, "wait": wait, "reason": "http429"})
                self._sleep(wait)
                attempt += 1
                continue
            except HttpError as exc:
                if attempt >= self.config.max_retries:
                    raise
                wait = min(2.0 ** attempt, 30.0)
                self.retries.append({"resource": resource, "attempt": attempt, "wait": wait, "reason": str(exc)})
                self._sleep(wait)
                attempt += 1
                continue
            if isinstance(answer, dict) and int(answer.get("code") if str(answer.get("code")).lstrip("-").isdigit() else -1) == 0:
                return answer
            retry_reason = self._handle_business_code(spec, answer if isinstance(answer, dict) else {}, attempt)
            if retry_reason is None:
                continue
            if attempt >= self.config.max_retries:
                raise RateLimitError("领星接口 %s 持续限流（%s）" % (spec.path, retry_reason), retry_after=1.0, status=429)
            wait = min(2.0 ** attempt, 30.0)
            self.retries.append({"resource": resource, "attempt": attempt, "wait": wait, "reason": retry_reason})
            self._sleep(wait)
            attempt += 1

    def fetch_page(self, resource: str, offset: int = 0, length: int = 0, extra_body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        spec = RESOURCE_SPECS[resource]
        size = length or min(self.config.page_size, spec.max_page_size)
        body = spec.default_body()
        body.update(extra_body or {})
        if resource != "customs_detail":
            body[spec.offset_key] = int(offset)
            body[spec.page_size_key] = int(size)
        return self.call(resource, body=body)

    def fetch_all(self, resource: str, extra_body: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """按 offset 翻页取全量；重复页视为异常（防止平台忽略 offset 造成死循环）。"""
        spec = RESOURCE_SPECS[resource]
        if spec.name == "customs_detail":
            return list(_dig(self.fetch_page(resource, extra_body=extra_body), spec.list_path) or [])
        out: List[Dict[str, Any]] = []
        offset = 0
        size = min(self.config.page_size, spec.max_page_size)
        seen: set = set()
        while True:
            answer = self.fetch_page(resource, offset=offset, length=size, extra_body=extra_body)
            items = list(_dig(answer, spec.list_path) or [])
            total = _dig(answer, spec.total_path)
            try:
                total_int = int(total)
            except (TypeError, ValueError):
                total_int = len(out) + len(items)
            key = tuple(str(item.get("id") or item.get("declarationId") or item.get("supplier_id") or item.get("sku")) for item in items)
            if items and key in seen:
                raise LingxingApiError("资源 %s 在 offset=%d 返回了重复页" % (resource, offset))
            seen.add(key)
            out.extend(items)
            offset += size
            if not items or len(out) >= total_int:
                break
        return out

    def batch_customs_detail(self, declaration_nos: Sequence[str]) -> List[Dict[str, Any]]:
        """报关资料明细：单次最多 50 个编号（官方文档字段说明）。"""
        numbers = [str(item) for item in declaration_nos if str(item).strip()]
        out: List[Dict[str, Any]] = []
        for start in range(0, len(numbers), 50):
            chunk = numbers[start:start + 50]
            answer = self.call("customs_detail", body={"declarationNos": chunk})
            out.extend(_dig(answer, ("data",)) or [])
        return out


def _sha256(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload or b"").hexdigest()


def client_from_env(env: Optional[Dict[str, str]] = None) -> LingxingApiClient:
    """只从环境变量造客户端。"""
    return LingxingApiClient(LingxingConfig.from_env(env))
