"""真实取数抓取器：把每一次真实请求与原始响应落盘，产出可核对、可重放、可自证的证据目录。

为什么要有这个脚本（买方第 2 次驳回第 2 条）：
  「一致率 1.000000 来自合成数据自比」与「缺真的取过数、真的写过台账的运行证据」是同一件事的两面。
  口头声称「调用路径是真的」不构成证据；能被别人复核的证据是：每一次真实 HTTP 请求的 URL、
  时间、HTTP 状态、原始响应体 sha256 与记录条数，逐条落盘，并且任何人都能拿这个目录离线重算。

用法（PowerShell）：
    # 领星：拉 产品 / 供应商 / 货件 / 报关资料，落盘到 captures/live/<UTC 时间戳>/
    python scripts/live_capture.py --source lingxing --out captures/live --env-file ../work/1364/.env.local

    # 钉钉：探令牌、列工作表、读区域、（可选）写一行并回读校验，验证台账写回这条路真的通
    python scripts/live_capture.py --source dingtalk --out captures/live/dingtalk --env-file ../work/1364/.env.local

    # 钉钉：探测「能不能建出自建表格」——把官方文档里的建表/建库入口逐条打一遍，
    # 平台拒绝记 denials[]（预期结果），不影响 complete；不写任何业务数据
    python scripts/live_capture.py --source dingtalk --out captures/dingtalk --env-file ../work/1364/.env.local --probe-create

    # 拿真实响应重算一致率（这一步才是 AC#1 的证据）
    python scripts/sample50.py --source capture --capture-dir captures/live/<时间戳>

落盘内容（每一个请求两份文件）：
    <seq>-<endpoint>.request.json    方法 / 路径 / query 名值（sign、app_key、operatorId 已打码）/
                                     header 名值（凭据类一律打码）/ body 键与 sha256
    <seq>-<endpoint>.response.json   平台原始响应体（令牌响应体不落盘，只留键名与 sha256）
    manifest.json                    origin=live、逐条 endpoint / 请求时间 / HTTP 状态 / 响应 sha256 / 记录条数

几种失败情形的行为（都不留半成品、都不冒充成功）：
  · 缺少 LINGXING_APP_ID / LINGXING_APP_SECRET   → 打印缺哪个变量，退出码 2，不创建目录；
  · 网络不通 / 域名不可达（TCP 超时）            → 把失败的那一次请求写成 failures[]（含错误原文），
                                                   manifest 标 complete=false，退出码 3；
  · 平台返回非 0 业务码或缺权限（403）           → 同样进 failures[]，manifest 标 complete=false，退出码 3。

凭据安全：
  · 凭据只从环境变量读；--env-file 只是把 .env.local 读进进程环境，不打印、不回写、不进 manifest；
  · manifest 里只记「凭据是否就位」的布尔值，从不记值；
  · accessToken / appSecret 永远不落盘：令牌响应体整段不写文件，Authorization 头一律打码。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.http_client import (  # noqa: E402
    HttpError,
    HttpRequest,
    HttpResponse,
    RateLimitError,
    UrllibTransport,
    _is_secret_header,
    json_request,
)
from core.lingxing_client import (  # noqa: E402
    DEFAULT_REFRESH_PATH,
    DEFAULT_TOKEN_PATH,
    RESOURCE_SPECS,
    LingxingApiClient,
    LingxingConfig,
    LingxingConfigError,
)

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_CONFIG = 2
EXIT_NETWORK = 3

CAPTURE_VERSION = 1

# 平台路径 -> 回放用的端点名。回放侧 core.sources 只认 products / suppliers / shipments / boxes / customs / customs_detail。
ENDPOINT_ROUTES: Tuple[Tuple[str, str], ...] = (
    (RESOURCE_SPECS["products"].path, "products"),
    (RESOURCE_SPECS["suppliers"].path, "suppliers"),
    (RESOURCE_SPECS["shipments"].path, "shipments"),
    (RESOURCE_SPECS["customs"].path, "customs"),
    (RESOURCE_SPECS["customs_detail"].path, "customs_detail"),
    ("/amzStaServer/openapi/inbound-shipment/listShipmentBoxes", "boxes"),
    # 钉钉侧（同一套落盘机制，端点名用于人工核对）
    ("/v1.0/oauth2/accessToken", "dingtalk_token"),
    ("/v1.0/doc/workbooks", "dingtalk_sheets"),
)

# 创建入口探测用到的路径：名字只用于人工核对，不参与回放（回放只认上面那六个资源）。
DINGTALK_PROBE_ROUTES: Tuple[Tuple[str, str], ...] = (
    ("/v2.0/wiki/workspaces", "dingtalk_wiki_workspaces"),
    ("/v2.0/wiki/mineWorkspaces", "dingtalk_wiki_mine"),
    ("/v1.0/contact/users/me", "dingtalk_contact_me"),
    ("/v1.0/drive/spaces", "dingtalk_drive_spaces"),
)

# 钉钉的 ranges 路径带变量段，按后缀规则单独判。
DINGTALK_RANGE_SUFFIX = "/ranges/"

# query 里这些参数一律打码：签名、应用身份、令牌、操作人身份（unionId 属个人信息）。
SECRET_QUERY_KEYS = frozenset(
    (
        "sign",
        "app_key",
        "appkey",
        "access_token",
        "accesstoken",
        "token",
        "app_secret",
        "appsecret",
        "refresh_token",
        "refreshtoken",
        "operatorid",
        "operator_id",
    )
)

# 这些端点的响应体含令牌，整段不落盘。
TOKEN_PATHS = frozenset((DEFAULT_TOKEN_PATH, DEFAULT_REFRESH_PATH, "/v1.0/oauth2/accessToken"))

# 请求体里这些键的值也算凭据：令牌换发接口的 body 就是 {appKey, appSecret}。
SECRET_BODY_KEYS = frozenset(
    (
        "appkey",
        "app_key",
        "appsecret",
        "app_secret",
        "secret",
        "clientsecret",
        "client_secret",
        "token",
        "accesstoken",
        "access_token",
        "refreshtoken",
        "refresh_token",
        "password",
        "operatorid",
        "operator_id",
    )
)


def _now_pair() -> Tuple[str, str]:
    """本地带时区的 ISO 时间与 UTC 时间：报告里两个都留，跨时区可核对。"""
    now = datetime.now(timezone.utc)
    return now.astimezone().isoformat(timespec="seconds"), now.isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload or b"").hexdigest()


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _path_of(url: str) -> str:
    return urlsplit(url).path or "/"


def _endpoint_name(url: str, method: str = "") -> str:
    """按平台路径判定端点名；判不出来就叫 unknown（不猜）。

    method 参与判定：POST /v1.0/doc/workbooks 是「建工作簿」（该接口不存在，实测 404），
    与 GET /v1.0/doc/workbooks/{id}/sheets「列工作表」是两件事，不能用同一个名字。
    """
    path = _path_of(url)
    verb = str(method or "").upper()
    if verb == "POST" and path == "/v1.0/doc/workbooks":
        return "dingtalk_workbook_create"
    if path.startswith("/v1.0/doc/workspaces/"):
        return "dingtalk_workspace_docs"
    for route, name in DINGTALK_PROBE_ROUTES:
        if path == route:
            return name
    if path.startswith("/v1.0/drive/spaces/") and path.endswith("/files"):
        return "dingtalk_drive_files"
    for route, name in ENDPOINT_ROUTES:
        if path == route or path.endswith(route):
            return name
    if DINGTALK_RANGE_SUFFIX in path:
        return "dingtalk_range"
    if path.startswith("/v1.0/doc/workbooks/"):
        return "dingtalk_sheets"
    return "unknown"


def _redact_query(url: str) -> List[Dict[str, str]]:
    """query 参数名值，凭据类值打码；值里的原文不进文件。"""
    out: List[Dict[str, str]] = []
    for name, value in parse_qsl(urlsplit(url).query, keep_blank_values=True):
        if name.lower() in SECRET_QUERY_KEYS:
            out.append({"name": name, "value": "***"})
        else:
            out.append({"name": name, "value": value})
    return out


# 文本里出现的 "operatorId=xxx" / "access_token=xxx" 这类片段也要打码：
# 平台的报错原文会把整条 URL（含 query）回显出来，那是个人身份信息。
_QUERY_IN_TEXT = re.compile(
    r"(%s)=([^&\s\"'<>)]{1,200})" % "|".join(sorted((re.escape(k) for k in SECRET_QUERY_KEYS), key=len, reverse=True)),
    re.IGNORECASE,
)


def _redact_url_query_in_text(text: str) -> str:
    return _QUERY_IN_TEXT.sub(lambda match: "%s=***" % match.group(1), text)


def _redact_headers(headers: Dict[str, str]) -> Dict[str, str]:
    """header 名值：凭据类（token / secret / sign / api-key / app-key）一律打码。"""
    out: Dict[str, str] = {}
    for name, value in (headers or {}).items():
        out[name] = "***" if _is_secret_header(name) else str(value)
    return out


def _scrub_body(value: Any) -> Any:
    """把请求体里凭据类的值换成 ***；键名保留，证明「这个字段确实发过」。"""
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in SECRET_BODY_KEYS:
                out[key] = "***"
            else:
                out[key] = _scrub_body(item)
        return out
    if isinstance(value, list):
        return [_scrub_body(item) for item in value]
    return value


def _body_document(request: HttpRequest) -> Tuple[Optional[Any], List[str], str, int]:
    """请求体：返回 (脱敏后的结构化 body, 键名列表, sha256, 字节数)；非 JSON 体只留 sha256。"""
    if not request.body:
        return None, [], "", 0
    digest = _sha256(request.body)
    size = len(request.body)
    try:
        parsed = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, [], digest, size
    if isinstance(parsed, dict):
        return _scrub_body(parsed), sorted(parsed.keys()), digest, size
    return _scrub_body(parsed), [], digest, size


def _response_document(response: HttpResponse) -> Tuple[Any, int]:
    """响应体：能解析成 JSON 就结构化保存，否则保留文本。返回 (文档, 字节数)。"""
    raw = response.body or b""
    try:
        return json.loads(raw.decode("utf-8")), len(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"_non_json_body": raw.decode("utf-8", errors="replace")}, len(raw)


def _record_count(name: str, payload: Any) -> int:
    """数出这份响应里有多少条记录；数不出来就是 0，不估。"""
    if isinstance(payload, list):
        return len(payload)
    if not isinstance(payload, dict):
        return 0
    if name == "sheets":
        sheets = payload.get("sheets") or payload.get("value") or []
        return len(sheets) if isinstance(sheets, list) else 0
    if name == "range":
        values = payload.get("values") or []
        return len(values) if isinstance(values, list) else 0
    data = payload.get("data")
    if isinstance(data, dict):
        if isinstance(data.get("list"), list):
            return len(data["list"])
        if isinstance(data.get("data"), list):
            return len(data["data"])
    if isinstance(data, list):
        return len(data)
    for key in ("list", "items", "records"):
        if isinstance(payload.get(key), list):
            return len(payload[key])
    return 0


def _business_code(payload: Any) -> Optional[Any]:
    if isinstance(payload, dict) and "code" in payload:
        return payload.get("code")
    return None


def _business_ok(payload: Any) -> bool:
    """领星业务码：0 才算成功；钉钉没有 code 字段，按 HTTP 状态判定。"""
    code = _business_code(payload)
    if code is None:
        return True
    return str(code).strip() in ("0", "200")

# 值两侧的引号：单引号 / 双引号（用 chr 拼，避免和 Python 的转义混在一起）。
QUOTES = chr(34) + chr(39)


def _parse_env_file(path: str) -> Dict[str, str]:
    """读 .env 形式文件：KEY=VALUE，# 开头忽略，值两侧引号去掉。只返回值，不打印、不落盘。"""
    values: Dict[str, str] = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            if text.lower().startswith("export "):
                text = text[7:].strip()
            if "=" not in text:
                continue
            key, value = text.split("=", 1)
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in QUOTES:
                value = value[1:-1]
            if key:
                values[key] = value
    return values


class CaptureRun:
    """一次抓取：管目录、管序号、逐请求落盘、最后写 manifest。"""

    def __init__(self, folder: str, source: str, host: str, notes: List[str], replayable: bool) -> None:
        self.folder = folder
        self.source = source
        self.host = host
        self.notes = list(notes)
        self.replayable = replayable
        self.created_at, self.created_at_utc = _now_pair()
        self.endpoints: List[Dict[str, Any]] = []
        self.failures: List[Dict[str, Any]] = []
        # 探测型抓取（--probe-create）：平台拒绝是「预期结果」，不是抓取失败。
        # 这类响应必须与成功响应一样逐字落盘，但不能让 manifest.complete 变 false，
        # 否则「探测做完了」和「抓取没做完」两件事会被混成一件。
        self.denials: List[Dict[str, Any]] = []
        self._secrets: List[str] = []
        self._seq = 0

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def add_secret(self, value: str) -> None:
        """登记一个绝不允许落盘的字符串（appId / appSecret / accessToken）。

        落盘前在整段 JSON 文本里做一次替换：这是兜底，防止凭据从某个没预料到的字段
        （例如平台错误信息里回显的 appId）漏进证据目录。
        """
        text = str(value or "").strip()
        if len(text) >= 6 and text not in self._secrets:
            self._secrets.append(text)

    def _redact_text(self, text: str) -> str:
        for secret in self._secrets:
            if secret in text:
                text = text.replace(secret, secret[:4] + "***" + secret[-2:])
        return text

    def _write(self, name: str, document: Any) -> str:
        text = json.dumps(document, ensure_ascii=False, indent=2)
        text = self._redact_text(_redact_url_query_in_text(text))
        with open(os.path.join(self.folder, name), "w", encoding="utf-8") as handle:
            handle.write(text)
        return name

    def _request_file(self, seq: int, name: str, request: HttpRequest) -> str:
        body, keys, digest, size = _body_document(request)
        parts = urlsplit(request.url)
        document = {
            "seq": seq,
            "endpoint": _path_of(request.url),
            "method": request.method,
            "url": "%s://%s%s" % (parts.scheme, parts.netloc, parts.path or "/"),
            "query": _redact_query(request.url),
            "headers": _redact_headers(request.headers),
            "body_keys": keys,
            "body_sha256": digest,
            "body_bytes": size,
            "body": body,
            "_note": "header / query / body 里的凭据类值一律打码（***）；其余为业务参数原文",
        }
        return self._write("%02d-%s.request.json" % (seq, name), document)

    def record_success(self, request: HttpRequest, response: HttpResponse, at_local: str, at_utc: str) -> None:
        name = _endpoint_name(request.url, request.method)
        seq = self._next_seq()
        request_file = self._request_file(seq, name, request)
        document, size = _response_document(response)
        if _path_of(request.url) in TOKEN_PATHS:
            # 令牌响应体整段不落盘：里面有 access_token / refresh_token。
            document = {
                "_redacted": "token response body is never persisted",
                "_keys": sorted(document.keys()) if isinstance(document, dict) else [],
                "_text_sha256": _sha256(response.body or b""),
                "_text_length": len(response.body or b""),
            }
        response_file = self._write("%02d-%s.response.json" % (seq, name), document)
        ok = int(response.status) < 400 and _business_ok(document)
        entry = {
            "seq": seq,
            "name": name,
            "endpoint": _path_of(request.url),
            "method": request.method,
            "requested_at": at_local,
            "requested_at_utc": at_utc,
            "status": response.status,
            "business_code": _business_code(document),
            "ok": ok,
            "response_sha256": _sha256(response.body or b""),
            "response_bytes": size,
            "record_count": _record_count(name, document),
            "total": document.get("total") if isinstance(document, dict) else None,
            "request_file": request_file,
            "response_file": response_file,
            "origin": "live",
        }
        if ok:
            self.endpoints.append(entry)
        else:
            entry["error"] = "HTTP %s / business code %s" % (response.status, entry["business_code"])
            self.failures.append(entry)

    def record_failure(self, request: HttpRequest, at_local: str, at_utc: str, status: int, body: str, error: str) -> None:
        """网络层失败（TCP 不可达 / 超时 / 403）也如实记录：这是证据，不是噪音。"""
        self._record_rejection(request, at_local, at_utc, status, body, error, bucket=self.failures, kind="failure")

    def record_denial(self, request: HttpRequest, at_local: str, at_utc: str, status: int, body: str, error: str) -> None:
        """探测请求被平台明确拒绝：原文照落盘，但不算抓取失败（--probe-create 用）。"""
        self._record_rejection(request, at_local, at_utc, status, body, error, bucket=self.denials, kind="denial")

    def _record_rejection(
        self,
        request: HttpRequest,
        at_local: str,
        at_utc: str,
        status: int,
        body: str,
        error: str,
        bucket: List[Dict[str, Any]],
        kind: str,
    ) -> None:
        name = _endpoint_name(request.url, request.method)
        seq = self._next_seq()
        request_file = self._request_file(seq, name, request)
        document: Dict[str, Any] = {"_transport_error": error, "status": status}
        if body:
            try:
                document["body"] = json.loads(body)
            except (TypeError, ValueError):
                document["raw"] = str(body)[:4000]
        response_file = self._write("%02d-%s.%s.json" % (seq, name, kind), document)
        raw = str(body or "").encode("utf-8")
        bucket.append({
            "seq": seq,
            "name": name,
            "endpoint": _path_of(request.url),
            "method": request.method,
            "requested_at": at_local,
            "requested_at_utc": at_utc,
            "status": status,
            "business_code": _business_code(document.get("body")),
            "ok": False,
            "error": error,
            "response_sha256": _sha256(raw),
            "response_bytes": len(raw),
            "record_count": 0,
            "request_file": request_file,
            "response_file": response_file,
            "origin": "live",
        })

    def manifest(self, derived: Optional[Dict[str, Any]] = None, warnings: Optional[List[str]] = None) -> Dict[str, Any]:
        counts: Dict[str, int] = {}
        for entry in self.endpoints:
            counts[entry["name"]] = counts.get(entry["name"], 0) + int(entry.get("record_count") or 0)
        complete = not self.failures
        document = {
            "origin": "live",
            "capture_version": CAPTURE_VERSION,
            "tool": "scripts/live_capture.py",
            "source": self.source,
            "host": self.host,
            "created_at": self.created_at,
            "created_at_utc": self.created_at_utc,
            "complete": complete,
            "replayable": bool(self.replayable and complete),
            "request_count": len(self.endpoints) + len(self.failures) + len(self.denials),
            "endpoint_count": len(self.endpoints),
            "failure_count": len(self.failures),
            "denial_count": len(self.denials),
            "endpoints": self.endpoints,
            "failures": self.failures,
            # 探测型抓取里「平台明确拒绝」是预期结果，原文照留但不影响 complete。
            "denials": self.denials,
            "record_counts": counts,
            "derived": derived or {},
            "warnings": list(warnings or []),
            "notes": self.notes,
            "credentials_note": "本目录不含 accessToken / appSecret / Client Secret；令牌响应体已脱敏不落盘",
        }
        self._write("manifest.json", document)
        return document


class CaptureTransport:
    """包住真实传输层：发出去的每个请求、收进来的每个响应都留给 CaptureRun。"""

    def __init__(self, run: CaptureRun, inner: Optional[Any] = None) -> None:
        self.run = run
        self.inner = inner or UrllibTransport()
        # 只有探测那一段把 4xx 记成 denials；其余步骤的 4xx 仍然是 failures。
        # 不给整个 run 开这个开关：读/写路径的失败不能因为「同时在探测」就被洗白。
        self.denial_mode = False

    def _record_rejection(
        self,
        request: HttpRequest,
        at_local: str,
        at_utc: str,
        status: int,
        body: str,
        error: str,
    ) -> None:
        """探测模式下 4xx 是「预期的拒绝」，只做留痕，不算抓取失败。"""
        if self.denial_mode and 400 <= int(status or 0) < 500:
            self.run.record_denial(request, at_local, at_utc, status, body, error)
        else:
            self.run.record_failure(request, at_local, at_utc, status, body, error)

    def __call__(self, request: HttpRequest) -> HttpResponse:
        at_local, at_utc = _now_pair()
        try:
            response = self.inner(request)
        except RateLimitError as exc:
            self._record_rejection(
                request, at_local, at_utc, int(getattr(exc, "status", 429) or 429),
                str(getattr(exc, "body", "") or ""), str(exc),
            )
            raise
        except HttpError as exc:
            self._record_rejection(
                request, at_local, at_utc, int(getattr(exc, "status", 0) or 0),
                str(getattr(exc, "body", "") or ""), str(exc),
            )
            raise
        self.run.record_success(request, response, at_local, at_utc)
        # 令牌响应里出现过的令牌，立刻登记成「绝不落盘」，后续文件的兜底替换会一并处理。
        try:
            parsed = json.loads((response.body or b"").decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = None
        if isinstance(parsed, dict):
            for key in ("accessToken", "access_token"):
                if parsed.get(key):
                    self.run.add_secret(str(parsed[key]))
            data = parsed.get("data")
            if isinstance(data, dict):
                for key in ("access_token", "refresh_token"):
                    if data.get(key):
                        self.run.add_secret(str(data[key]))
        return response


def _make_folder(out_root: str) -> str:
    """一个抓取一个目录；同名加后缀，绝不覆盖历史证据。"""
    base = os.path.join(out_root, _stamp())
    folder = base
    index = 2
    while os.path.exists(folder):
        folder = "%s-%d" % (base, index)
        index += 1
    os.makedirs(folder)
    return folder

def _window_from_env(args: argparse.Namespace) -> Dict[str, str]:
    """货件列表必填的 sid 与日期区间：命令行优先，其次环境变量。"""
    return {
        "sid": str(args.sid or os.environ.get("LINGXING_SID", "")).strip(),
        "start_date": str(args.start_date or os.environ.get("LINGXING_START_DATE", "")).strip(),
        "end_date": str(args.end_date or os.environ.get("LINGXING_END_DATE", "")).strip(),
    }


def capture_lingxing(args: argparse.Namespace, out_root: str) -> int:
    """用与 --source lingxing 完全相同的取数逻辑跑一遍，只是把每个请求都录下来。"""
    from core import sources

    try:
        config = LingxingConfig.from_env()
        config.require_credentials()
    except LingxingConfigError as exc:
        print("无法开始抓取：%s" % exc, file=sys.stderr)
        print("提示：把 LINGXING_APP_ID / LINGXING_APP_SECRET 放进环境变量，或用 --env-file 指向 .env.local", file=sys.stderr)
        return EXIT_CONFIG

    window = _window_from_env(args)
    if not args.skip_shipments:
        missing = [name for name, value in window.items() if not value]
        if missing:
            print(
                "无法开始抓取：货件列表接口必填 sid 与日期区间，缺 LINGXING_SID / LINGXING_START_DATE / LINGXING_END_DATE"
                "（也可用 --sid / --start-date / --end-date 传入）",
                file=sys.stderr,
            )
            return EXIT_CONFIG

    notes = [
        "抓取走的是 core.sources.load_from_lingxing()，与 --source lingxing 同一段取数逻辑",
        "签名 v2：MD5(32 位大写) -> AES/ECB/PKCS5(密钥=appId) -> Base64 -> URL 编码；token 走 Authorization: Bearer",
    ]
    if args.skip_shipments:
        notes.append("本次跳过货件列表（--skip-shipments），回放时 batches 会为空")

    run = CaptureRun(_make_folder(out_root), "lingxing", config.base_url, notes, replayable=not args.skip_shipments)
    # appId / appSecret 一旦出现在任何字符串里（例如平台错误信息回显），落盘前都替换掉。
    run.add_secret(config.app_id)
    run.add_secret(config.app_secret)
    client = LingxingApiClient(config, http=CaptureTransport(run))

    saved = dict(os.environ)
    if not args.skip_shipments:
        os.environ.update({
            "LINGXING_SID": window["sid"],
            "LINGXING_START_DATE": window["start_date"],
            "LINGXING_END_DATE": window["end_date"],
        })
    try:
        batches, products, suppliers, provenance = sources.load_from_lingxing(client)
    except Exception as exc:
        os.environ.clear()
        os.environ.update(saved)
        manifest = run.manifest(warnings=[str(exc)])
        print("抓取失败：%s" % exc, file=sys.stderr)
        print(
            "已把失败现场写进 %s（complete=false、failure_count=%d）" % (run.folder, manifest["failure_count"]),
            file=sys.stderr,
        )
        return EXIT_NETWORK if manifest["failure_count"] else EXIT_FAIL
    os.environ.clear()
    os.environ.update(saved)

    warnings: List[str] = []
    if provenance.get("mapping_notes"):
        warnings.extend(provenance["mapping_notes"])
    derived = {
        "batches": len(batches),
        "products": len(products),
        "suppliers": len(suppliers),
        "host": provenance.get("host", config.base_url),
    }
    manifest = run.manifest(derived=derived, warnings=warnings)
    print("capture_dir %s" % run.folder)
    print(
        "origin %s complete %s endpoints %d failures %d requests %d"
        % (
            manifest["origin"],
            str(manifest["complete"]).lower(),
            manifest["endpoint_count"],
            manifest["failure_count"],
            manifest["request_count"],
        )
    )
    print("records %s" % json.dumps(manifest["record_counts"], ensure_ascii=False, sort_keys=True))
    print("derived batches %d products %d suppliers %d" % (derived["batches"], derived["products"], derived["suppliers"]))
    print("下一步 python scripts/sample50.py --source capture --capture-dir %s" % os.path.relpath(run.folder, ROOT))
    return EXIT_OK if manifest["complete"] else EXIT_NETWORK


# 自建表格能不能建出来：官方文档里所有「创建」入口逐条打一遍。
# 每条都真的发出去，成功/拒绝都按原文落盘；这里不接受任何「大概是 403」的猜测。
CREATE_PROBES: Tuple[Tuple[str, str, str, Optional[Dict[str, Any]]], ...] = (
    ("列出知识库（工作簿只能建在知识库里）", "GET", "/v2.0/wiki/workspaces?maxResults=5", None),
    ("我的文档（同上，另一入口）", "GET", "/v2.0/wiki/mineWorkspaces", None),
    ("建知识库", "POST", "/v2.0/wiki/workspaces", {"name": "clawhunt-probe-space"}),
    (
        "知识库根目录建表格文档",
        "POST",
        "/v1.0/doc/workspaces/PROBE-WORKSPACE-ID/docs",
        {"name": "clawhunt-probe-sheet", "docType": "WORKBOOK", "operatorId": "PROBE-NOT-A-REAL-UNIONID"},
    ),
    ("直接建工作簿（文档里没有这个接口）", "POST", "/v1.0/doc/workbooks", {"name": "clawhunt-probe-sheet"}),
    ("建云盘空间", "POST", "/v1.0/drive/spaces", {"name": "clawhunt-probe-space", "unionId": "PROBE-NOT-A-REAL-UNIONID"}),
    ("云盘空间里建文件", "POST", "/v1.0/drive/spaces/PROBE-SPACE-ID/files", {"name": "clawhunt-probe-sheet", "fileType": "file"}),
    ("读当前用户（想拿 unionId 走这条）", "GET", "/v1.0/contact/users/me", None),
)


def _probe_create(config: Any, transport: Any, token: str) -> List[str]:
    """逐条打「创建」入口，返回人读结论行；每次请求/响应的原文由 CaptureTransport 落盘。"""
    lines: List[str] = []
    transport.denial_mode = True
    try:
        return _probe_loop(config, transport, token, lines)
    finally:
        transport.denial_mode = False


def _probe_loop(config: Any, transport: Any, token: str, lines: List[str]) -> List[str]:
    for label, method, path, payload in CREATE_PROBES:
        url = config.base_url + path
        headers = {"x-acs-dingtalk-access-token": token}
        try:
            response = json_request(transport, method, url, headers=headers, payload=payload, timeout=config.timeout)
        except HttpError as exc:
            body = _safe_payload(getattr(exc, "body", "") or "")
            detail = _denial_detail(body)
            lines.append(
                "%s → HTTP %s %s（%s）" % (label, getattr(exc, "status", 0), _code_of(body), detail)
            )
            continue
        lines.append("%s → HTTP %s 成功 %s" % (label, response.status, _code_of(_safe_payload(response.text()))))
    return lines


def _safe_payload(text: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _code_of(body: Dict[str, Any]) -> str:
    code = body.get("code") or body.get("message") or ""
    return str(code)[:60]


def _denial_detail(body: Dict[str, Any]) -> str:
    """把 403 的 requiredScopes / 400 的 message 压成一句人读的话。"""
    detail = body.get("accessdenieddetail") or body.get("accessDeniedDetail") or {}
    scopes = detail.get("requiredScopes") if isinstance(detail, dict) else None
    if scopes:
        return "缺权限 %s" % ",".join(str(item) for item in scopes)
    message = str(body.get("message") or "").strip()
    return message[:120]


def _write_and_read_back(client: Any, workbook_id: str, sheet_id: str, sample: List[List[Any]]) -> Dict[str, Any]:
    """写 A1:B1，再把同一区域读回来比对：只有读回的值与写入一致才算这条链路通。

    只写不读不算跑通——写入接口返回 200 不代表表里真的有这几个字。
    """
    address = "A1:B1"
    client.write_range(address, sample, workbook_id=workbook_id, sheet_id=sheet_id)
    readback = client.read_range(address, workbook_id=workbook_id, sheet_id=sheet_id)
    first = list(readback[0]) if readback else []
    expected = list(sample[0])
    same = [str(cell) for cell in first] == [str(cell) for cell in expected]
    if not same:
        # 写成功但读回来不一样，就是没写进去；这种情况必须报错，不能只说 ok。
        raise RuntimeError(
            "写入后回读不一致：写入 %r，读回 %r（区域 %s）"
            % ([str(cell) for cell in expected], [str(cell) for cell in first], address)
        )
    return {
        "written": expected,
        "readback": [str(cell) for cell in first],
        "match": same,
        "range": address,
        "sheetId": sheet_id,
    }


LEDGER_HEADER = ["批次号", "SKU", "产品中文报关名", "HSCODE"]


def _write_ledger_row(
    client: Any,
    workbook_id: str,
    sheet_id: str,
    row_key: str,
    values: Dict[str, Any],
) -> Dict[str, Any]:
    """用交付里的同一段 write_row 真写一行台账，再读回来逐格比对。

    与 --write-sample 的区别：--write-sample 只证明「这个区域能写」，
    这里走的是台账真正用的那条路径——先确认表头（没有就补表头），
    再按第一列定位行号、PUT 整行、把写进去的那一行读回来比对。
    只写不读不算跑通：PUT 返回 200 不代表表里真有这几个字。
    """
    from core.dingtalk_client import range_address

    header = client.read_range("A1:D1", workbook_id=workbook_id, sheet_id=sheet_id)
    current = [str(cell) for cell in (header[0] if header else [])]
    previous = list(current)
    header_written = False
    if [cell.strip() for cell in current[: len(LEDGER_HEADER)]] != LEDGER_HEADER:
        # 表头不是台账要的四列（新表通常是空的，也可能残留探测写入），先把表头写正。
        client.write_range(
            "A1:D1", [LEDGER_HEADER], workbook_id=workbook_id, sheet_id=sheet_id
        )
        header_written = True
        current = list(LEDGER_HEADER)
    answer = client.write_row(row_key, values, workbook_id=workbook_id, sheet_id=sheet_id)
    address = str(answer.get("range") or "")
    if not address:
        raise RuntimeError("write_row 没有返回写入区域，无法回读校验")
    readback = client.read_range(address, workbook_id=workbook_id, sheet_id=sheet_id)
    first = [str(cell) for cell in (readback[0] if readback else [])]
    expected = [str(values.get(name, "")) for name in current]
    same = first[: len(expected)] == expected
    if not same:
        raise RuntimeError(
            "台账写入后回读不一致：写入 %r，读回 %r（区域 %s）" % (expected, first, address)
        )
    return {
        "mode": "ledger_row",
        "rowKey": str(row_key),
        "headerWritten": header_written,
        "previousHeader": previous,
        "header": list(current),
        "written": expected,
        "readback": first[: len(expected)],
        "match": same,
        "range": address,
        "sheetId": sheet_id,
    }


def _probe_leading_zero(
    client: Any,
    workbook_id: str,
    sheet_id: str,
    value: str,
    start_row: int = 20,
) -> Dict[str, Any]:
    """实测「0 开头的编码会不会被平台吃掉前导零」，并把三种写法的结果读回来对照。

    背景（本轮真踩到的）：HSCODE 存成 `0902100000`，直接写进去回读是 `902100000`——
    报关编码前导零一丢就是另一个税号，而接口照样返回 200。这条探测把三种写法的**真实结果**
    落成证据，也是 `core.dingtalk_client.keep_text()` 存在的理由。
    """
    # 第一组必须绕开客户端的前导零保护（as_text=False），否则测的是「我们已经修好了」而不是平台真实行为。
    variants = [
        ("platform_default", value, False),
        ("apostrophe_text", "'%s" % value, True),
        ("formula_text", '="%s"' % value, True),
    ]
    results: Dict[str, Any] = {"value": value, "variants": []}
    for offset, (label, written, as_text) in enumerate(variants):
        row = start_row + offset
        address = "A%d:A%d" % (row, row)
        client.write_range(
            address, [[written]], workbook_id=workbook_id, sheet_id=sheet_id, as_text=as_text
        )
        readback = client.read_range(address, workbook_id=workbook_id, sheet_id=sheet_id)
        got = str((readback[0] or [""])[0]) if readback else ""
        results["variants"].append(
            {
                "label": label,
                "range": address,
                "sent": written,
                "readback": got,
                "preserved": got == value,
            }
        )
    results["any_preserved"] = any(item["preserved"] for item in results["variants"])
    return results


def _dingtalk_derived(
    placeholder: bool,
    probing: bool,
    self_built: bool,
    operator_configured: bool,
    write_evidence: Optional[Dict[str, Any]] = None,
    required_scopes: Optional[List[str]] = None,
    target: Optional[Dict[str, Any]] = None,
    leading_zero_probe: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """manifest.derived 的构造：把「这批证据到底算不算客户证据」写成字段，不靠正文里的口头声明。"""
    if required_scopes is None:
        from core.dingtalk_client import SCOPE_READ, SCOPE_WRITE

        required_scopes = [SCOPE_READ, SCOPE_WRITE]
    derived: Dict[str, Any] = {
        "workbook_id_configured": not placeholder,
        "placeholder_ids_used": placeholder,
        "required_scopes": list(required_scopes),
        "operator_id_configured": operator_configured,
        "probe_create": probing,
        "create_endpoints_probed": len(CREATE_PROBES) if probing else 0,
        # 自建测试表 vs 客户台账：只有后者能当 AC 证据，前者只能证明链路通。
        # 探测建表（--probe-create）必然是我方自建环境；占位 id 下连表都没摸到，
        # 也谈不上「写没写过客户台账」，同样不能算客户证据。
        "environment": "self_built_test_table" if (self_built or probing) else "",
        "counts_as_customer_evidence": (not self_built) and (not placeholder),
    }
    if target:
        # 记「写的是哪张表」：workbookId / sheetId 不是凭据，留着别人才能对上号。
        derived["target"] = dict(target)
    if write_evidence:
        derived["write_evidence"] = dict(write_evidence)
    if leading_zero_probe:
        derived["leading_zero_probe"] = dict(leading_zero_probe)
    return derived


def capture_dingtalk(args: argparse.Namespace, out_root: str) -> int:
    """台账侧真实证据：换令牌、列工作表、读区域、（可选）写一行。"""
    from core.dingtalk_client import (
        SCOPE_APPLY_URL,
        SCOPE_READ,
        SCOPE_WRITE,
        DingTalkApiError,
        DingTalkClient,
        DingTalkConfig,
        DingTalkConfigError,
    )

    try:
        config = DingTalkConfig.from_env()
        config.require_credentials()
    except DingTalkConfigError as exc:
        print("无法开始抓取：%s" % exc, file=sys.stderr)
        return EXIT_CONFIG

    operator_id = str(getattr(args, "operator_id", "") or config.operator_id or "").strip()
    if operator_id:
        config.operator_id = operator_id
    workbook_id = str(args.workbook_id or config.workbook_id or "").strip()
    sheet_id = str(args.sheet_id or config.sheet_id or "").strip()
    placeholder = False
    if not workbook_id or not sheet_id:
        # workbookId / sheetId 未配置时用占位值探权限：缺 scope 时平台先校验权限、与 id 是否存在无关，
        # 这样得到的 403 原文可以作为「凭据有效、权限未授予」的证据。
        placeholder = True
        workbook_id = workbook_id or "PLACEHOLDER-WORKBOOK-ID"
        sheet_id = sheet_id or "PLACEHOLDER-SHEET-ID"

    notes = [
        "钉钉侧证据：令牌换发 + 工作表/区域读 +（可选）区域写，逐次落盘",
        "缺权限时平台返回 403 Forbidden.AccessDenied.AccessTokenPermissionDenied，本脚本把原文与 requiredScopes 一起留下",
    ]
    if placeholder:
        notes.append("workbookId/sheetId 未配置，用占位值探测权限范围（占位 id 下的 403 不代表表不存在）")
    if not config.operator_id:
        notes.append(
            "operatorId（操作人 unionId）未配置：读接口可通过，写接口会被平台以 "
            "400 MissingoperatorId 拒绝——写证据必须配齐 DINGTALK_OPERATOR_ID"
        )

    probing = bool(getattr(args, "probe_create", False))
    if probing:
        notes.append(
            "本目录是「创建入口探测」：逐条打官方文档里的建表/建库入口，"
            "平台拒绝属于预期结果，记在 denials[] 而不是 failures[]；不写任何业务数据"
        )
    if getattr(args, "self_built_table", False):
        notes.append(
            "本目录写入的是我方自建测试表格（environment=self_built_test_table），"
            "只证明「凭据有效 + 权限到位 + 写入并读回一致」这条链路通，"
            "不是客户台账，也不作为 AC#1/AC#7 的验收证据"
        )
    if (args.write_sample or getattr(args, "write_ledger_row", "")) and placeholder:
        # 占位 id 写不了：真去写只会拿到 operatorId / 表不存在一类的错误，看起来像「写不了」，
        # 其实是「没给 id」。直接在开目录之前拒掉，不留空目录。
        print(
            "无法开始写入：%s 需要真实的 workbookId / sheetId"
            "（占位 id 只用于探测权限，不能写）"
            % ("--write-ledger-row" if getattr(args, "write_ledger_row", "") else "--write-sample"),
            file=sys.stderr,
        )
        return EXIT_CONFIG
    if getattr(args, "write_ledger_row", "") and not config.operator_id:
        # 写台账必须带 operatorId（操作人 unionId），没有就写不了；提前说清楚，别写一半。
        print(
            "无法开始写入台账：缺少 DINGTALK_OPERATOR_ID（操作人 unionId），"
            "写入接口的 query 必须带它",
            file=sys.stderr,
        )
        return EXIT_CONFIG

    run = CaptureRun(
        _make_folder(out_root),
        "dingtalk",
        config.base_url,
        notes,
        replayable=False,
    )
    # appKey / appSecret 出现在任何字符串里都替换掉（钉钉的 403 原文会回显 appKey）。
    run.add_secret(config.app_key)
    run.add_secret(config.app_secret)
    transport = CaptureTransport(run)
    client = DingTalkClient(config, http=transport)

    results: List[str] = []
    try:
        token = client.access_token()
        results.append("access_token ok length=%d" % len(token))
    except Exception as exc:
        results.append("access_token failed: %s" % exc)
        run.manifest(warnings=[str(exc)])
        for line in results:
            print(line)
        print("capture_dir %s" % run.folder)
        print("令牌没有换到，后续步骤全部跳过（complete=false）", file=sys.stderr)
        return EXIT_CONFIG

    probe_lines: List[str] = []
    if probing:
        probe_lines = _probe_create(config, transport, token)
        results.extend(probe_lines)

    steps: List[Tuple[str, Any]] = [
        ("list_sheets", lambda: client.list_sheets(workbook_id=workbook_id)),
        ("read_range", lambda: client.read_range("A1:Z3", workbook_id=workbook_id, sheet_id=sheet_id)),
    ]
    if probing and placeholder:
        # 探测的目的是「能不能建出表」，用占位 id 去读只会必然拿到 operatorId 参数错误，
        # 那种失败会把 complete 拉成 false，反而盖住探测本身的结论。
        steps = []
        results.append("list_sheets/read_range 跳过：本目录是创建入口探测，workbookId 尚未拿到")
    write_evidence: Dict[str, Any] = {}
    zero_evidence: Dict[str, Any] = {}
    if args.write_sample:
        sample = [["capture-probe", args.write_sample]]
        steps.append(("write_range", lambda: _write_and_read_back(client, workbook_id, sheet_id, sample)))
    if getattr(args, "probe_leading_zero", ""):
        # 前导零探测：三种写法各写一次、各读回一次，结果进 manifest.derived
        steps.append(
            (
                "probe_leading_zero",
                lambda: _probe_leading_zero(
                    client, workbook_id, sheet_id, args.probe_leading_zero
                ),
            )
        )
    if getattr(args, "write_ledger_row", ""):
        # 台账真路径：表头 → 定位行号 → PUT 整行 → 读回比对，走的是交付里的 write_row。
        ledger_values = {
            "批次号": args.write_ledger_row,
            "SKU": str(getattr(args, "ledger_sku", "") or "BND-2S"),
            "产品中文报关名": str(getattr(args, "ledger_name", "") or "桌面组合套装A"),
            "HSCODE": str(getattr(args, "ledger_hscode", "") or "3924900000"),
        }
        steps.append(
            (
                "write_ledger_row",
                lambda: _write_ledger_row(
                    client, workbook_id, sheet_id, args.write_ledger_row, ledger_values
                ),
            )
        )

    for name, call in steps:
        try:
            answer = call()
            if name == "probe_leading_zero" and isinstance(answer, dict):
                zero_evidence = dict(answer)
            if name in ("write_range", "write_ledger_row") and isinstance(answer, dict):
                # 写入证据必须包含「写进去什么 / 读回来什么 / 是否一致」，只写不读不算跑通。
                write_evidence = dict(answer)
            if isinstance(answer, list):
                results.append("%s ok items=%d" % (name, len(answer)))
            else:
                results.append("%s ok keys=%d" % (name, len(dict(answer).keys())))
        except DingTalkApiError as exc:
            scope = exc.missing_scope
            if scope:
                results.append(
                    "%s denied scope=%s apply=%s"
                    % (name, scope, SCOPE_APPLY_URL % scope.replace("#", "%23"))
                )
            else:
                results.append("%s failed: %s" % (name, exc))
        except Exception as exc:
            results.append("%s failed: %s" % (name, exc))

    # 探测建表 / 显式声明自建表 / 只用占位 id 探权限——这三种都不是客户台账。
    self_built = bool(getattr(args, "self_built_table", False)) or probing
    derived = _dingtalk_derived(
        placeholder=placeholder,
        probing=probing,
        self_built=self_built,
        operator_configured=bool(config.operator_id),
        write_evidence=write_evidence,
        leading_zero_probe=zero_evidence,
        target={
            "workbookId": workbook_id,
            "sheetId": sheet_id,
            "redactedOperatorId": "***" if config.operator_id else "",
        }
        if not placeholder
        else None,
    )
    manifest = run.manifest(
        derived=derived,
        warnings=[text for text in results if "failed" in text or "denied" in text],
    )
    for line in results:
        print(line)
    print("capture_dir %s" % run.folder)
    print(
        "origin %s complete %s endpoints %d failures %d requests %d"
        % (
            manifest["origin"],
            str(manifest["complete"]).lower(),
            manifest["endpoint_count"],
            manifest["failure_count"],
            manifest["request_count"],
        )
    )
    if manifest["failure_count"]:
        print("存在失败/缺权限的请求：原文见 failures[].response_file，可据此申请权限", file=sys.stderr)
        return EXIT_NETWORK
    return EXIT_OK


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python scripts/live_capture.py")
    parser.add_argument("--source", default="lingxing", choices=["lingxing", "dingtalk"], help="抓哪个平台")
    parser.add_argument("--out", default="", help="抓取根目录，默认 captures/live；每次抓取在其下建一个 UTC 时间戳子目录")
    parser.add_argument("--env-file", default="", help="把 KEY=VALUE 文件读进进程环境（不打印、不落盘），例如 .env.local 的绝对路径")
    parser.add_argument("--sid", default="", help="领星店铺 sid（货件列表必填），也可用 LINGXING_SID")
    parser.add_argument("--start-date", default="", dest="start_date", help="货件起始日期 Y-m-d（左闭），也可用 LINGXING_START_DATE")
    parser.add_argument("--end-date", default="", dest="end_date", help="货件结束日期 Y-m-d（右开），也可用 LINGXING_END_DATE")
    parser.add_argument("--skip-shipments", action="store_true", help="只抓产品/供应商，不抓货件（回放时 batches 为空）")
    parser.add_argument("--workbook-id", default="", dest="workbook_id", help="钉钉工作簿 id（缺省取 DINGTALK_WORKBOOK_ID）")
    parser.add_argument("--sheet-id", default="", dest="sheet_id", help="钉钉工作表 id（缺省取 DINGTALK_SHEET_ID）")
    parser.add_argument("--operator-id", default="", dest="operator_id", help="钉钉操作人 unionId（缺省取 DINGTALK_OPERATOR_ID），读写区域接口必填")
    parser.add_argument("--write-sample", default="", dest="write_sample", help="给定时真的写一行探测值，验证写权限（写进 A1:B1）")
    parser.add_argument(
        "--write-ledger-row",
        default="",
        dest="write_ledger_row",
        help="按台账真路径写一行（表头->定位行->PUT 整行->读回校验），参数是批次号",
    )
    parser.add_argument(
        "--probe-leading-zero",
        default="",
        dest="probe_leading_zero",
        help="探测 0 开头的编码会不会被平台吃掉前导零：三种写法各写一次读一次，结果进 manifest",
    )
    parser.add_argument("--ledger-sku", default="", dest="ledger_sku", help="--write-ledger-row 的 SKU")
    parser.add_argument("--ledger-name", default="", dest="ledger_name", help="--write-ledger-row 的产品中文报关名")
    parser.add_argument("--ledger-hscode", default="", dest="ledger_hscode", help="--write-ledger-row 的 HSCODE")
    parser.add_argument(
        "--self-built-table",
        action="store_true",
        dest="self_built_table",
        help="声明目标是我方自建测试表格：manifest 记 environment=self_built_test_table、counts_as_customer_evidence=false",
    )
    parser.add_argument(
        "--probe-create",
        action="store_true",
        dest="probe_create",
        help="逐条探测建表/建库入口（只发创建请求，不写业务数据）；平台拒绝记 denials[] 不影响 complete",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.env_file:
        path = os.path.abspath(args.env_file)
        if not os.path.exists(path):
            print("无法开始抓取：--env-file 指向的文件不存在：%s" % path, file=sys.stderr)
            return EXIT_CONFIG
        for key, value in _parse_env_file(path).items():
            # 命令行里显式给的环境变量优先，不被文件覆盖。
            os.environ.setdefault(key, value)

    out_root = os.path.abspath(args.out or os.path.join(ROOT, "captures", "live"))
    if args.source == "dingtalk":
        return capture_dingtalk(args, out_root)
    return capture_lingxing(args, out_root)


if __name__ == "__main__":
    sys.exit(main())