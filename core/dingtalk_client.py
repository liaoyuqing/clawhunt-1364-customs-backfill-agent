"""钉钉开放平台客户端：在线表格（workbooks / sheets / ranges）读写，逐个字段对齐官方文档。

协议依据（官方文档，均可在公网免密钥读取）：
- 鉴权：POST {base}/v1.0/oauth2/accessToken，body {"appKey","appSecret"}，返回 {expireIn, accessToken}；
  之后每个业务请求带请求头 x-acs-dingtalk-access-token: <accessToken>。
  https://open.dingtalk.com/document/orgapp/obtain-the-access_token-of-an-internal-app
- 工作表列表：GET {base}/v1.0/doc/workbooks/{workbookId}/sheets
  https://open.dingtalk.com/document/orgapp/obtain-a-list-of-sheets
- 读取单元格区域：GET {base}/v1.0/doc/workbooks/{workbookId}/sheets/{sheetId}/ranges/{rangeAddress}
  返回 {"values": [[...], ...]}（二维数组，行优先）。
  https://open.dingtalk.com/document/orgapp/read-cell-properties
- 更新单元格区域：PUT 同一条 ranges 路径，**query 必须带 operatorId（操作人 unionId）**，
  body {"values": [[...], ...]}，与读取同构。
  https://open.dingtalk.com/document/development/update-cell-properties
- 上一轮用的 /v1.0/doc/sheets/{sheet_id}/rows 是错路径（平台返回 InvalidAction.NotFound），已废弃。
  权限范围：读需 Document.Workbook.Read，写需 Document.Workbook.Write；缺权限时平台返回
  403 Forbidden.AccessDenied.AccessTokenPermissionDenied，本客户端把 requiredScopes 原样报出来。

凭据只从环境变量读，仓库里没有任何字面量凭据。具体缺失哪个变量、缺哪个权限，错误信息里直接写明。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote

from .http_client import HttpError, HttpRequest, RateLimitError, Transport, UrllibTransport, json_request

ENV_BASE_URL = "DINGTALK_BASE_URL"
ENV_APP_KEY = "DINGTALK_APP_KEY"
ENV_APP_KEY_ALIAS = "DINGTALK_CLIENT_ID"
ENV_APP_SECRET = "DINGTALK_APP_SECRET"
ENV_APP_SECRET_ALIAS = "DINGTALK_CLIENT_SECRET"
ENV_WORKBOOK_ID = "DINGTALK_WORKBOOK_ID"
ENV_SHEET_ID = "DINGTALK_SHEET_ID"
ENV_OPERATOR_ID = "DINGTALK_OPERATOR_ID"
ENV_HEADER_RANGE = "DINGTALK_HEADER_RANGE"
ENV_READ_RANGE = "DINGTALK_READ_RANGE"

DEFAULT_BASE_URL = "https://api.dingtalk.com"
DEFAULT_TOKEN_PATH = "/v1.0/oauth2/accessToken"
DEFAULT_SHEETS_PATH = "/v1.0/doc/workbooks/{workbook_id}/sheets"
DEFAULT_RANGE_PATH = "/v1.0/doc/workbooks/{workbook_id}/sheets/{sheet_id}/ranges/{range_address}"
# 表头在第一行；读回现有行时按 A..Z 覆盖 23 列台账（多留余量，平台按实际表宽截断）。
DEFAULT_HEADER_RANGE = "A1:Z1"
DEFAULT_READ_RANGE = "A1:Z300"

# 官方文档里这两条权限是分开申请的：读一次、写一次。
SCOPE_READ = "Document.Workbook.Read"
SCOPE_WRITE = "Document.Workbook.Write"
SCOPE_APPLY_URL = "https://open-dev.dingtalk.com/appscope/apply?content=%s"


class DingTalkConfigError(RuntimeError):
    """必需的环境变量缺失或格式不对。"""


class DingTalkApiError(RuntimeError):
    """平台返回非预期结果（业务错误、缺权限等）。"""

    def __init__(self, message: str, payload: Optional[Dict[str, Any]] = None, status: int = 0) -> None:
        super().__init__(message)
        self.payload = payload or {}
        self.status = status

    @property
    def missing_scope(self) -> str:
        """平台没给权限时，把官方 requiredScopes 取出来，便于直接去申请。

        实测平台的 403 返回体把 scope 放在 accessdenieddetail.requiredScopes 里
        （顶层没有），两个位置都要看。
        """
        candidates = [self.payload]
        detail = self.payload.get("accessdenieddetail") or self.payload.get("accessDeniedDetail")
        if isinstance(detail, dict):
            candidates.append(detail)
        for source in candidates:
            for key in ("requiredScopes", "required_scope", "RequiredScopes"):
                value = source.get(key)
                if isinstance(value, list) and value:
                    return str(value[0])
                if isinstance(value, str) and value:
                    return value
        return ""


def _first_env(source: Dict[str, str], names: Sequence[str]) -> str:
    for name in names:
        value = str(source.get(name, "")).strip()
        if value:
            return value
    return ""


def column_letter(index: int) -> str:
    """列序号（1 起）转 Excel 列字母：1 -> A，27 -> AA。"""
    if index <= 0:
        raise ValueError("column index must be 1 or greater")
    letters = ""
    current = int(index)
    while current > 0:
        current, remainder = divmod(current - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def range_address(start_row: int, end_row: int, columns: int) -> str:
    """构造区域地址，形如 A1:W3（1 起、含端点）。"""
    if columns <= 0:
        raise ValueError("columns must be 1 or greater")
    last = column_letter(columns)
    return "A%d:%s%d" % (start_row, last, end_row)


@dataclass
class DingTalkConfig:
    """连接设置；每个字段都从环境变量读，仓库里不含凭据。"""

    base_url: str = DEFAULT_BASE_URL
    app_key: str = ""
    app_secret: str = ""
    workbook_id: str = ""
    sheet_id: str = ""
    operator_id: str = ""
    token_path: str = DEFAULT_TOKEN_PATH
    sheets_path: str = DEFAULT_SHEETS_PATH
    range_path: str = DEFAULT_RANGE_PATH
    header_range: str = DEFAULT_HEADER_RANGE
    read_range_address: str = DEFAULT_READ_RANGE
    max_retries: int = 4
    timeout: float = 30.0

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "DingTalkConfig":
        source = env if env is not None else os.environ
        base_url = str(source.get(ENV_BASE_URL, "") or DEFAULT_BASE_URL).strip().rstrip("/")
        return cls(
            base_url=base_url,
            app_key=_first_env(source, (ENV_APP_KEY, ENV_APP_KEY_ALIAS)),
            app_secret=_first_env(source, (ENV_APP_SECRET, ENV_APP_SECRET_ALIAS)),
            workbook_id=str(source.get(ENV_WORKBOOK_ID, "")).strip(),
            sheet_id=str(source.get(ENV_SHEET_ID, "")).strip(),
            operator_id=str(source.get(ENV_OPERATOR_ID, "")).strip(),
            header_range=str(source.get(ENV_HEADER_RANGE, "") or DEFAULT_HEADER_RANGE).strip(),
            read_range_address=str(source.get(ENV_READ_RANGE, "") or DEFAULT_READ_RANGE).strip(),
        )

    def require_credentials(self) -> None:
        missing = []
        if not self.app_key:
            missing.append("%s（或 %s）" % (ENV_APP_KEY, ENV_APP_KEY_ALIAS))
        if not self.app_secret:
            missing.append("%s（或 %s）" % (ENV_APP_SECRET, ENV_APP_SECRET_ALIAS))
        if missing:
            raise DingTalkConfigError("缺少环境变量：%s" % "、".join(missing))

    def header_address(self) -> str:
        """表头所在区域地址（默认第一行）。"""
        return self.header_range

    def read_address(self) -> str:
        """读回现有行的区域地址（默认 A1:Z300）。"""
        return self.read_range_address

    def require_target(self, workbook_id: str = "", sheet_id: str = "") -> Tuple[str, str]:
        book = workbook_id or self.workbook_id
        sheet = sheet_id or self.sheet_id
        missing = []
        if not book:
            missing.append(ENV_WORKBOOK_ID)
        if not sheet:
            missing.append(ENV_SHEET_ID)
        if missing:
            raise DingTalkConfigError(
                "缺少环境变量：%s（workbookId 可取知识库节点 nodeId 或开放平台工作簿 id）" % "、".join(missing)
            )
        return book, sheet


@dataclass
class DingTalkClient:
    """真实客户端：令牌换发与缓存、范围读写、限流退避、缺权限明确报错。"""

    config: DingTalkConfig
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

    # ---- 令牌 ----
    def access_token(self, force: bool = False) -> str:
        """换发并缓存企业内部应用令牌（有效期 expireIn 秒，官方默认 7200）。"""
        now = float(self._clock())
        if not force and self._token and now < self._token_expire_at:
            return self._token
        self.config.require_credentials()
        url = self.config.base_url + self.config.token_path
        payload = {"appKey": self.config.app_key, "appSecret": self.config.app_secret}
        body = self._send("POST", url, payload=payload)
        token = str(body.get("accessToken", "")).strip()
        if not token:
            raise DingTalkApiError("令牌接口没有返回 accessToken：%s" % json.dumps(body, ensure_ascii=False)[:200])
        try:
            ttl = float(body.get("expireIn") or 7200)
        except (TypeError, ValueError):
            ttl = 7200.0
        self._token = token
        self._token_expire_at = now + max(ttl - 60.0, 60.0)
        self.token_events.append({"action": "access-token", "expire_in": body.get("expireIn"), "token_length": len(token)})
        return token

    # ---- 通用发送 ----
    def _send(self, method: str, url: str, payload: Any = None, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        attempt = 0
        while True:
            merged = {"Accept": "application/json"}
            if payload is not None:
                merged["Content-Type"] = "application/json"
            merged.update(headers or {})
            try:
                """每次真实发出的请求都留痕：方法、URL、鉴权头名（值脱敏）、body 键名。"""
                response = json_request(self.http, method, url, headers=merged, payload=payload, timeout=self.config.timeout)
                self.requests.append(
                    HttpRequest(method=method, url=url, headers=merged, timeout=self.config.timeout).describe()
                )
                self.responses.append({"url": url, "status": response.status})
            except RateLimitError as exc:
                if attempt >= self.config.max_retries:
                    raise
                wait = float(getattr(exc, "retry_after", 1.0) or 1.0)
                self.retries.append({"url": url, "attempt": attempt, "wait": wait, "reason": "http429"})
                self._sleep(wait)
                attempt += 1
                continue
            except HttpError as exc:
                """403 + AccessTokenPermissionDenied 是缺权限，重试没有意义，直接把 scope 报出来。"""
                payload_text = str(getattr(exc, "body", "") or "")
                if "AccessTokenPermissionDenied" in payload_text:
                    raise DingTalkApiError(
                        "钉钉接口缺权限（%s）：%s" % (self._describe_url(url), self._scope_hint(payload_text)),
                        payload=_safe_json(payload_text),
                        status=int(getattr(exc, "status", 0) or 0),
                    )
                if attempt >= self.config.max_retries:
                    raise
                self.retries.append({"url": url, "attempt": attempt, "wait": 1.0, "error": str(exc)[:200]})
                self._sleep(1.0)
                attempt += 1
                continue
            body = response.json()
            return dict(body) if isinstance(body, dict) else {"data": body}

    @staticmethod
    def _describe_url(url: str) -> str:
        return url.split("?")[0]

    @staticmethod
    def _scope_hint(payload_text: str) -> str:
        parsed = _safe_json(payload_text)
        detail = parsed.get("accessdenieddetail") or parsed.get("accessDeniedDetail") or {}
        scopes = ""
        for source in (parsed, detail if isinstance(detail, dict) else {}):
            scopes = source.get("requiredScopes") or source.get("required_scope") or scopes
        if isinstance(scopes, list):
            scope = str(scopes[0]) if scopes else ""
        else:
            scope = str(scopes)
        if scope:
            return "缺少权限 %s，申请链接 %s" % (scope, SCOPE_APPLY_URL % quote(scope, safe=""))
        return payload_text[:300]

    # ---- 工作表与区域 ----
    def list_sheets(self, workbook_id: str = "") -> List[Dict[str, Any]]:
        """列出工作簿下的工作表（官方 GET /v1.0/doc/workbooks/{id}/sheets）。

        这条路径只需要 workbookId，不需要 sheetId；缺 workbookId 时单独报清楚。
        """
        book = str(workbook_id or self.config.workbook_id or "").strip()
        if not book:
            raise DingTalkConfigError(
                "缺少环境变量：%s（workbookId 可取知识库节点 nodeId 或开放平台工作簿 id）" % ENV_WORKBOOK_ID
            )
        token = self.access_token()
        url = self.config.base_url + self.config.sheets_path.format(workbook_id=book)
        body = self._send("GET", url, headers={"x-acs-dingtalk-access-token": token})
        sheets = body.get("sheets") or body.get("value") or body.get("data") or []
        if isinstance(sheets, dict):
            sheets = sheets.get("sheets") or sheets.get("value") or []
        return [dict(item) for item in sheets if isinstance(item, dict)]

    def _range_url(self, address: str, workbook_id: str = "", sheet_id: str = "") -> str:
        book, sheet = self.config.require_target(workbook_id=workbook_id, sheet_id=sheet_id)
        path = self.config.range_path.format(
            workbook_id=book,
            sheet_id=sheet,
            range_address=quote(str(address), safe=""),
        )
        return self.config.base_url + path

    def read_range(self, address: str, workbook_id: str = "", sheet_id: str = "") -> List[List[Any]]:
        """读一个区域，返回二维数组（官方返回体 {"values": [[...]]}）。"""
        token = self.access_token()
        url = self._range_url(address, workbook_id=workbook_id, sheet_id=sheet_id)
        body = self._send("GET", url, headers={"x-acs-dingtalk-access-token": token})
        values = body.get("values")
        if not isinstance(values, list):
            data = body.get("data")
            if isinstance(data, dict):
                values = data.get("values")
        return [list(row) if isinstance(row, (list, tuple)) else [row] for row in (values or [])]

    def write_range(
        self,
        address: str,
        values: List[List[Any]],
        workbook_id: str = "",
        sheet_id: str = "",
        operator_id: str = "",
    ) -> Dict[str, Any]:
        """写一个区域（官方 PUT，query 必带 operatorId）。"""
        operator = operator_id or self.config.operator_id
        if not operator:
            raise DingTalkConfigError(
                "缺少环境变量：%s（写入接口的 operatorId 是操作人 unionId，必填）" % ENV_OPERATOR_ID
            )
        token = self.access_token()
        url = self._range_url(address, workbook_id=workbook_id, sheet_id=sheet_id)
        url = "%s?operatorId=%s" % (url, quote(str(operator), safe=""))
        return self._send("PUT", url, payload={"values": values}, headers={"x-acs-dingtalk-access-token": token})

    # ---- 台账语义（供 core.sources.SheetSink 复用） ----
    def read_rows(self, sheet_id: str = "", workbook_id: str = "") -> List[Dict[str, Any]]:
        """把区域里的第一行当表头，读回成 [{列名: 值}]；空表返回空列表。"""
        book, sheet = self.config.require_target(workbook_id=workbook_id, sheet_id=sheet_id)
        table = self.read_range(self.config.read_range_address, workbook_id=book, sheet_id=sheet)
        if not table:
            return []
        header = [str(cell) for cell in table[0]]
        rows: List[Dict[str, Any]] = []
        for raw in table[1:]:
            item = {}
            for index, name in enumerate(header):
                if not name:
                    continue
                item[name] = raw[index] if index < len(raw) else ""
            if any(str(value).strip() for value in item.values()):
                rows.append(item)
        return rows

    def write_row(self, row_key: str, values: Dict[str, Any], sheet_id: str = "", workbook_id: str = "") -> Dict[str, Any]:
        """写一行台账：按第一列（批次号）定位行号，找不到就追加一行。

        行号定位靠一次真实读取，不猜；写入是官方 PUT ranges，body 为二维数组。
        """
        book, sheet = self.config.require_target(workbook_id=workbook_id, sheet_id=sheet_id)
        header_row = self.read_range(self.config.header_range, workbook_id=book, sheet_id=sheet)
        header = [str(cell) for cell in (header_row[0] if header_row else [])]
        if not header:
            raise DingTalkApiError("目标表第一行为空，无法确定列序（%s）" % self.config.header_address())
        columns: List[Any] = []
        for name in header:
            columns.append(values.get(name, ""))
        existing = self.read_range(self.config.read_address(), workbook_id=book, sheet_id=sheet)
        row_number = 0
        for offset, raw in enumerate(existing[1:], start=2):
            if raw and str(raw[0]).strip() == str(row_key):
                row_number = offset
                break
        if row_number == 0:
            row_number = max(len(existing) + 1, 2)
        address = range_address(row_number, row_number, len(header))
        answer = self.write_range(address, [columns], workbook_id=book, sheet_id=sheet)
        answer = dict(answer)
        answer.setdefault("sheetId", sheet)
        answer.setdefault("rowKey", str(row_key))
        answer["range"] = address
        return answer


def _safe_json(text: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def client_from_env(env: Optional[Dict[str, str]] = None) -> DingTalkClient:
    """只从环境变量造客户端。"""
    return DingTalkClient(DingTalkConfig.from_env(env))
