"""HTTP base: standard library urllib only, no new dependency.

Real platform calls go through here. The transport is injectable, so tests can record
exactly what was sent: URL, method, auth header names (values redacted), body shape.
Recorded responses are replayed through the same HttpResponse shape.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

DEFAULT_TIMEOUT = 30.0

# Header names whose values are never printed: credentials and signatures.
REDACTED_HEADERS = frozenset(
    (
        "authorization",
        "access-token",
        "app-key",
        "sign",
        "x-lingxing-token",
        "x-acs-dingtalk-access-token",
        "x-etax-access-token",
        "x-vision-api-key",
        "x-computer-use-api-key",
        "x-local-model-api-key",
    )
)


def _is_secret_header(name: str) -> bool:
    """True when the header carries a credential, a token, a secret or a signature."""
    lowered = name.lower()
    if lowered in REDACTED_HEADERS:
        return True
    return any(word in lowered for word in ("token", "secret", "password", "api-key", "appsecret"))


class HttpError(RuntimeError):
    """HTTP call failed (not a rate limit)."""

    def __init__(self, message: str, status: int = 0, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class RateLimitError(HttpError):
    """HTTP 429 or a platform specific rate limit code."""

    def __init__(self, message: str, retry_after: float = 1.0, status: int = 429, body: str = "") -> None:
        super().__init__(message, status=status, body=body)
        self.retry_after = retry_after


@dataclass
class HttpRequest:
    method: str
    url: str
    headers: Dict[str, str] = field(default_factory=dict)
    body: Optional[bytes] = None
    timeout: float = DEFAULT_TIMEOUT

    def body_json(self) -> Any:
        if not self.body:
            return None
        return json.loads(self.body.decode("utf-8"))

    def describe(self, redact: bool = True) -> Dict[str, Any]:
        """Printable request description; auth header values are always redacted."""
        headers = {}
        for key, value in self.headers.items():
            lowered = key.lower()
            if redact and _is_secret_header(lowered):
                headers[key] = "***"
            else:
                headers[key] = value
        payload = None
        if self.body:
            try:
                parsed = json.loads(self.body.decode("utf-8"))
                payload = sorted(parsed.keys()) if isinstance(parsed, dict) else type(parsed).__name__
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = "<non-json>"
        return {"method": self.method, "url": self.url, "headers": headers, "body_keys": payload}


@dataclass
class HttpResponse:
    status: int
    headers: Dict[str, str] = field(default_factory=dict)
    body: bytes = b""

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.text())


class UrllibTransport:
    """Default implementation over urllib.request; no third party dependency."""

    def __call__(self, request: HttpRequest) -> HttpResponse:
        req = urllib.request.Request(request.url, data=request.body, method=request.method)
        for key, value in request.headers.items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=request.timeout) as resp:
                return HttpResponse(resp.status, dict(resp.headers), resp.read())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code == 429:
                raw = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    wait = float(raw) if raw else 1.0
                except (TypeError, ValueError):
                    wait = 1.0
                raise RateLimitError("http 429 from %s" % request.url, retry_after=wait, status=429, body=body)
            raise HttpError("http %d from %s" % (exc.code, request.url), status=exc.code, body=body)
        except urllib.error.URLError as exc:
            raise HttpError("network error for %s: %s" % (request.url, exc))


class RecordingTransport:
    """Records every request and replays canned responses from a queue.

    Recorded items expose method / URL / header names (auth values redacted) / body
    shape, so a test can print the actual traffic as evidence.
    """

    def __init__(self, responses: Optional[List[Any]] = None) -> None:
        self.responses: List[Any] = list(responses or [])
        self.requests: List[HttpRequest] = []

    def __call__(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        if not self.responses:
            raise HttpError("no canned response left for %s %s" % (request.method, request.url))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def described(self) -> List[Dict[str, Any]]:
        return [request.describe() for request in self.requests]


Transport = Callable[[HttpRequest], HttpResponse]


def json_request(
    transport: Transport,
    method: str,
    url: str,
    headers: Optional[Dict[str, str]] = None,
    payload: Any = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> HttpResponse:
    """Send one request; the response body must be JSON."""
    body = None
    request_headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    for key, value in (headers or {}).items():
        request_headers[key] = value
    request = HttpRequest(method=method, url=url, headers=request_headers, body=body, timeout=timeout)
    response = transport(request)
    try:
        response.json()
    except json.JSONDecodeError as exc:
        raise HttpError("response is not JSON from %s: %s" % (url, exc), status=response.status, body=response.text())
    return response


def parse_json_response(response: HttpResponse) -> Any:
    """Parse a response body as JSON."""
    try:
        return response.json()
    except json.JSONDecodeError as exc:
        raise HttpError("response is not JSON: %s" % exc, status=response.status, body=response.text())
