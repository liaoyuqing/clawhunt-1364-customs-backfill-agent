"""Real DingTalk open platform client (sheets / rows).

Endpoints and credentials come from the environment only; nothing is stored in the
repository. The three path templates are overridable so a tenant running a
different platform version can point the client at its own routes without code
changes:

- DINGTALK_BASE_URL   open platform root, e.g. https://api.dingtalk.com
- DINGTALK_APP_KEY / DINGTALK_APP_SECRET   application credentials
- DINGTALK_SHEET_ID   target sheet (document) id
- DINGTALK_TOKEN_PATH / DINGTALK_WRITE_PATH   optional route overrides

Token acquisition, retry and rate limit handling are shared with the Lingxing
client through core.http_client.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .http_client import HttpError, HttpRequest, RateLimitError, Transport, UrllibTransport, json_request

ENV_BASE_URL = "DINGTALK_BASE_URL"
ENV_APP_KEY = "DINGTALK_APP_KEY"
ENV_APP_SECRET = "DINGTALK_APP_SECRET"
ENV_SHEET_ID = "DINGTALK_SHEET_ID"
ENV_TOKEN_PATH = "DINGTALK_TOKEN_PATH"
ENV_WRITE_PATH = "DINGTALK_WRITE_PATH"
ENV_LIST_PATH = "DINGTALK_LIST_PATH"

DEFAULT_TOKEN_PATH = "/v1.0/oauth2/accessToken"
DEFAULT_WRITE_PATH = "/v1.0/doc/sheets/{sheet_id}/rows"
DEFAULT_LIST_PATH = "/v1.0/doc/sheets/{sheet_id}/rows"


class DingTalkConfigError(RuntimeError):
    """Required environment variable missing."""


@dataclass
class DingTalkConfig:
    """Connection settings; every field is read from the environment."""

    base_url: str
    app_key: str = ""
    app_secret: str = ""
    sheet_id: str = ""
    token_path: str = DEFAULT_TOKEN_PATH
    write_path: str = DEFAULT_WRITE_PATH
    list_path: str = DEFAULT_LIST_PATH
    max_retries: int = 4
    timeout: float = 30.0

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "DingTalkConfig":
        source = env if env is not None else os.environ
        base_url = str(source.get(ENV_BASE_URL, "")).strip().rstrip("/")
        if not base_url:
            raise DingTalkConfigError("missing environment variable %s" % ENV_BASE_URL)
        return cls(
            base_url=base_url,
            app_key=str(source.get(ENV_APP_KEY, "")).strip(),
            app_secret=str(source.get(ENV_APP_SECRET, "")).strip(),
            sheet_id=str(source.get(ENV_SHEET_ID, "")).strip(),
            token_path=str(source.get(ENV_TOKEN_PATH, DEFAULT_TOKEN_PATH)).strip() or DEFAULT_TOKEN_PATH,
            write_path=str(source.get(ENV_WRITE_PATH, DEFAULT_WRITE_PATH)).strip() or DEFAULT_WRITE_PATH,
            list_path=str(source.get(ENV_LIST_PATH, DEFAULT_LIST_PATH)).strip() or DEFAULT_LIST_PATH,
        )


@dataclass
class DingTalkClient:
    """Real client: token exchange, retry with rate limit handling, row write."""

    config: DingTalkConfig
    http: Transport = field(default_factory=UrllibTransport)
    sleep: Optional[Any] = None
    clock: Optional[Any] = None
    requests: List[Dict[str, Any]] = field(default_factory=list)
    retries: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._sleep = self.sleep or time.sleep
        self._clock = self.clock or time.time

    def access_token(self) -> str:
        """Exchange app key/secret for an access token (cached for the process)."""
        if getattr(self, "_token", ""):
            return self._token
        if not (self.config.app_key and self.config.app_secret):
            raise DingTalkConfigError("missing %s or %s" % (ENV_APP_KEY, ENV_APP_SECRET))
        url = "%s%s" % (self.config.base_url, self.config.token_path)
        payload = {"appKey": self.config.app_key, "appSecret": self.config.app_secret}
        body = self._send("POST", url, payload=payload)
        token = str(body.get("accessToken", "")).strip()
        if not token:
            raise DingTalkConfigError("token exchange returned no accessToken")
        self._token = token
        return token

    def _send(self, method: str, url: str, payload: Any = None, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        attempt = 0
        while True:
            try:
                merged = {"Content-Type": "application/json"}
                merged.update(headers or {})
                request = HttpRequest(
                    method=method,
                    url=url,
                    headers=merged,
                    body=None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    timeout=self.config.timeout,
                )
                """每次真实发出的请求都留痕：URL、方法、鉴权头名（值脱敏）、body 结构。"""
                self.requests.append(request.describe())
                response = json_request(self.http, method, url, headers=merged, payload=payload, timeout=self.config.timeout)
            except RateLimitError as exc:
                if attempt >= self.config.max_retries:
                    raise
                self.retries.append({"url": url, "attempt": attempt, "wait": exc.retry_after})
                self._sleep(float(exc.retry_after))
                attempt += 1
                continue
            except HttpError as exc:
                if attempt >= self.config.max_retries:
                    raise
                self.retries.append({"url": url, "attempt": attempt, "wait": 1.0, "error": str(exc)})
                self._sleep(1.0)
                attempt += 1
                continue
            body = response.json()
            if isinstance(body, dict) and body.get("errcode") not in (None, 0):
                code = int(body.get("errcode", 0))
                if code in (429, 50021) and attempt < self.config.max_retries:
                    self.retries.append({"url": url, "attempt": attempt, "wait": 1.0, "code": code})
                    self._sleep(1.0)
                    attempt += 1
                    continue
                raise DingTalkApiError("dingtalk returned errcode=%s errmsg=%s" % (code, body.get("errmsg")), payload=body)
            return dict(body)


    def read_rows(self, sheet_id: str = "") -> List[Dict[str, Any]]:
        """Read the existing rows of the target sheet; returns the raw row list."""
        target_sheet = sheet_id or self.config.sheet_id
        if not target_sheet:
            raise DingTalkConfigError("missing %s" % ENV_SHEET_ID)
        token = self.access_token()
        url = "%s%s" % (self.config.base_url, self.config.list_path.format(sheet_id=target_sheet))
        headers = {"x-acs-dingtalk-access-token": token}
        body = self._send("GET", url, headers=headers)
        data = body.get("data")
        rows: Any = None
        if isinstance(data, dict):
            rows = data.get("rows") or data.get("list")
        if rows is None:
            rows = body.get("rows") or body.get("list")
        if not isinstance(rows, list):
            return []
        return [dict(item) for item in rows if isinstance(item, dict)]

    def write_row(self, row_key: str, values: Dict[str, Any], sheet_id: str = "") -> Dict[str, Any]:
        """Write one ledger row into the target sheet; returns the raw platform answer."""
        target_sheet = sheet_id or self.config.sheet_id
        if not target_sheet:
            raise DingTalkConfigError("missing %s" % ENV_SHEET_ID)
        token = self.access_token()
        url = "%s%s" % (self.config.base_url, self.config.write_path.format(sheet_id=target_sheet))
        payload = {"row_key": row_key, "values": values}
        headers = {"x-acs-dingtalk-access-token": token}
        body = self._send("POST", url, payload=payload, headers=headers)
        return body


class DingTalkApiError(RuntimeError):
    """The open platform returned a non-zero business code."""

    def __init__(self, message: str, payload: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.payload = payload or {}


def client_from_env(env: Optional[Dict[str, str]] = None) -> DingTalkClient:
    """Build a client from environment variables only."""
    return DingTalkClient(DingTalkConfig.from_env(env))