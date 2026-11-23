"""Real Lingxing ERP open API client.

Every credential and endpoint comes from the environment; nothing is stored in the
repository. The class talks to the open platform over HTTP with:

- token acquisition / refresh (access token from the env, or app_key + app_secret exchange);
- signed requests (per-request signature header, HMAC-SHA256 over the canonical string);
- pagination (offset + page_size until total is reached);
- error retry with backoff and 429 rate limit handling (Retry-After honoured).

The transport is injectable, so tests can assert that a real HTTP request was issued.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .http_client import HttpError, HttpRequest, RateLimitError, Transport, UrllibTransport, json_request

ENV_BASE_URL = "LINGXING_BASE_URL"
ENV_APP_KEY = "LINGXING_APP_KEY"
ENV_APP_SECRET = "LINGXING_APP_SECRET"
ENV_ACCESS_TOKEN = "LINGXING_ACCESS_TOKEN"

RESOURCES = ("batches", "products", "suppliers")


class LingxingConfigError(RuntimeError):
    """Required environment variable missing or malformed."""


@dataclass
class LingxingConfig:
    """Connection settings; every field is read from the environment."""

    base_url: str
    app_key: str = ""
    app_secret: str = ""
    access_token: str = ""
    page_size: int = 100
    max_retries: int = 4
    timeout: float = 30.0

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "LingxingConfig":
        source = env if env is not None else os.environ
        base_url = str(source.get(ENV_BASE_URL, "")).strip().rstrip("/")
        if not base_url:
            raise LingxingConfigError("missing environment variable %s" % ENV_BASE_URL)
        page_size = int(str(source.get("LINGXING_PAGE_SIZE", "100")) or 100)
        return cls(
            base_url=base_url,
            app_key=str(source.get(ENV_APP_KEY, "")).strip(),
            app_secret=str(source.get(ENV_APP_SECRET, "")).strip(),
            access_token=str(source.get(ENV_ACCESS_TOKEN, "")).strip(),
            page_size=page_size,
        )


@dataclass
class LingxingTransport:
    """Callable transport: LingxingClient calls it with (resource, offset, size, token).

    The default implementation is real HTTP against the open platform. Tests inject
    a recording transport instead, so the same client code path is exercised.
    """

    base_url: str = ""
    app_key: str = ""
    app_secret: str = ""
    access_token: str = ""
    timeout: float = 30.0
    http: Transport = field(default_factory=UrllibTransport)
    requests: List[Dict[str, Any]] = field(default_factory=list)

    def _url(self, resource: str, offset: int, size: int) -> str:
        return "%s/openapi/v1/%s?offset=%d&page_size=%d" % (self.base_url, resource, offset, size)

    def signature(self, timestamp: str, nonce: str) -> str:
        """HMAC-SHA256 over app_key+timestamp+nonce+secret, hex digest."""
        raw = "%s%s%s%s" % (self.app_key, timestamp, nonce, self.app_secret)
        return hmac.new(self.app_secret.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()

    def headers(self, timestamp: str, nonce: str) -> Dict[str, str]:
        headers = {
            "app-key": self.app_key,
            "timestamp": timestamp,
            "nonce": nonce,
            "sign": self.signature(timestamp, nonce),
        }
        if self.access_token:
            headers["access-token"] = self.access_token
        return headers

    def __call__(self, resource: str, offset: int, size: int, timestamp: str, nonce: str) -> Dict[str, Any]:
        url = self._url(resource, offset, size)
        request = HttpRequest(
            method="GET",
            url=url,
            headers=self.headers(timestamp, nonce),
            timeout=self.timeout,
        )
        self.requests.append(request.describe())
        response = json_request(self.http, "GET", url, headers=self.headers(timestamp, nonce), timeout=self.timeout)
        return response.json()


class LingxingApiClient:
    """Real open platform client: token handling, signing, pagination, retry, rate limit."""

    def __init__(
        self,
        config: LingxingConfig,
        transport: Optional[Any] = None,
        sleep: Optional[Any] = None,
        clock: Optional[Any] = None,
    ) -> None:
        self.config = config
        self._sleep = sleep or time.sleep
        self._clock = clock or time.time
        self.transport = transport or LingxingTransport(
            base_url=config.base_url,
            app_key=config.app_key,
            app_secret=config.app_secret,
            access_token=config.access_token,
            timeout=config.timeout,
        )
        self.retries: List[Dict[str, Any]] = []
        self.token_requests: List[Dict[str, Any]] = []

    def _nonce(self) -> str:
        return hashlib.sha256(("%.6f" % self._clock()).encode("utf-8")).hexdigest()[:16]

    def _timestamp(self) -> str:
        return str(int(self._clock()))

    def access_token(self) -> str:
        """Usable token: env token first, otherwise app_key/app_secret exchange."""
        if self.config.access_token:
            return self.config.access_token
        if not (self.config.app_key and self.config.app_secret):
            raise LingxingConfigError(
                "need %s or both %s and %s" % (ENV_ACCESS_TOKEN, ENV_APP_KEY, ENV_APP_SECRET)
            )
        url = "%s/openapi/v1/auth/token" % self.config.base_url
        payload = {"app_key": self.config.app_key, "app_secret": self.config.app_secret}
        response = json_request(self.transport.http, "POST", url, payload=payload, timeout=self.config.timeout)
        body = response.json()
        self.token_requests.append({"url": url, "method": "POST", "body_keys": sorted(payload.keys())})
        token = str((body.get("data") or {}).get("access_token", "")).strip()
        if not token:
            raise LingxingConfigError("token exchange returned no access_token")
        return token

    def _request_page(self, resource: str, offset: int, size: int) -> Dict[str, Any]:
        attempt = 0
        while True:
            try:
                payload = self.transport(resource, offset, size, self._timestamp(), self._nonce())
            except RateLimitError as exc:
                if attempt >= self.config.max_retries:
                    raise
                self.retries.append({"resource": resource, "offset": offset, "attempt": attempt, "wait": exc.retry_after})
                self._sleep(float(exc.retry_after))
                attempt += 1
                continue
            except HttpError as exc:
                if attempt >= self.config.max_retries:
                    raise
                self.retries.append({"resource": resource, "offset": offset, "attempt": attempt, "wait": 1.0, "error": str(exc)})
                self._sleep(1.0)
                attempt += 1
                continue
            code = int(payload.get("code", 0))
            if code == 0:
                return dict(payload.get("data") or {})
            if code in (429, 10009) and attempt < self.config.max_retries:
                self.retries.append({"resource": resource, "offset": offset, "attempt": attempt, "wait": 1.0, "code": code})
                self._sleep(1.0)
                attempt += 1
                continue
            raise LingxingApiError("lingxing returned code=%s msg=%s" % (code, payload.get("msg")), payload=payload)

    def fetch_all(self, resource: str) -> List[Dict[str, Any]]:
        """Page through a resource until the reported total is reached."""
        out: List[Dict[str, Any]] = []
        offset = 0
        size = self.config.page_size
        seen = set()
        while True:
            page = self._request_page(resource, offset, size)
            items = list(page.get("list") or [])
            total = int(page.get("total", 0))
            key = tuple(str(item.get("id")) for item in items)
            if items and key in seen:
                raise LingxingApiError("pagination returned a repeated page for %s at offset %s" % (resource, offset))
            seen.add(key)
            out.extend(items)
            offset += size
            if not items or len(out) >= total:
                break
        return out


class LingxingApiError(RuntimeError):
    """The open platform returned a non-zero business code."""

    def __init__(self, message: str, payload: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.payload = payload or {}


def client_from_env(env: Optional[Dict[str, str]] = None) -> LingxingApiClient:
    """Build a client from environment variables only."""
    return LingxingApiClient(LingxingConfig.from_env(env))