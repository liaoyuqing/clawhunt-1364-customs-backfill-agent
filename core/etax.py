"""模块三电子税务局半自动填单：生成申报单草稿，人工核对通过后才允许提交。

接口地址与凭据只从环境变量读；没有凭据时只产出草稿与待提交清单，绝不假装提交。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .http_client import HttpError, Transport, UrllibTransport, json_request

ENV_BASE_URL = "ETAX_BASE_URL"
ENV_APP_KEY = "ETAX_APP_KEY"
ENV_APP_SECRET = "ETAX_APP_SECRET"
ENV_TOKEN = "ETAX_TOKEN"
ENV_SUBMIT_PATH = "ETAX_SUBMIT_PATH"

DEFAULT_SUBMIT_PATH = "/v1/declaration/submit"

ACTION_SUBMIT = "etax_submit"


class EtaxConfigError(RuntimeError):
    """电子税务局接口配置缺失。"""


class EtaxApiError(RuntimeError):
    """电子税务局返回了业务失败码。"""

    def __init__(self, message: str, payload: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.payload = payload or {}


@dataclass
class EtaxConfig:
    """连接参数，全部来自环境变量。"""

    base_url: str
    app_key: str = ""
    app_secret: str = ""
    token: str = ""
    submit_path: str = DEFAULT_SUBMIT_PATH
    timeout: float = 30.0

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "EtaxConfig":
        source = env if env is not None else os.environ
        base_url = str(source.get(ENV_BASE_URL, "")).strip().rstrip("/")
        if not base_url:
            raise EtaxConfigError("missing environment variable %s" % ENV_BASE_URL)
        return cls(
            base_url=base_url,
            app_key=str(source.get(ENV_APP_KEY, "")).strip(),
            app_secret=str(source.get(ENV_APP_SECRET, "")).strip(),
            token=str(source.get(ENV_TOKEN, "")).strip(),
            submit_path=str(source.get(ENV_SUBMIT_PATH, DEFAULT_SUBMIT_PATH)).strip() or DEFAULT_SUBMIT_PATH,
        )

    def url(self) -> str:
        return "%s%s" % (self.base_url, self.submit_path)


def build_draft(
    batch_no: str,
    contract: Any,
    invoice_view: Dict[str, str],
    lines: Optional[Sequence[Any]] = None,
    declaration_date: str = "",
) -> Dict[str, Any]:
    """把合同与发票字段映射成申报单草稿；金额与明细逐行展开。"""
    rows: List[Dict[str, str]] = []
    source_lines = list(lines if lines is not None else getattr(contract, "lines", []))
    for line in source_lines:
        qty = str(getattr(line, "qty", ""))
        price = str(getattr(line, "unit_price", ""))
        try:
            amount = "%.2f" % (float(qty) * float(price))
        except (TypeError, ValueError):
            amount = ""
        rows.append(
            {
                "sku": str(getattr(line, "sku", "")),
                "product_name": str(getattr(line, "product_name", "")),
                "spec": str(getattr(line, "spec", "")),
                "quantity": qty,
                "unit": str(getattr(line, "unit", "")),
                "unit_price": price,
                "amount": amount,
            }
        )
    return {
        "batch_no": str(batch_no),
        "contract_no": str(getattr(contract, "contract_no", "")),
        "supplier": str(getattr(contract, "supplier", "")),
        "buyer": str(getattr(contract, "buyer", "")),
        "invoice_no": str(invoice_view.get("invoice_no", "")),
        "buyer_name": str(invoice_view.get("buyer_name", "")),
        "buyer_tax_no": str(invoice_view.get("buyer_tax_no", "")),
        "invoice_type": str(invoice_view.get("invoice_type", "")),
        "amount": str(invoice_view.get("amount", "")),
        "declaration_date": str(declaration_date or invoice_view.get("issued_at", "")),
        "items": rows,
    }


@dataclass
class EtaxClient:
    """真实电子税务局客户端；transport 可注入，测试能记录发出的 HTTP 请求。"""

    config: EtaxConfig
    http: Transport = field(default_factory=UrllibTransport)
    requests: List[Dict[str, Any]] = field(default_factory=list)

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.token:
            headers["x-etax-access-token"] = str(self.config.token)
        elif self.config.app_key and self.config.app_secret:
            headers["Authorization"] = "Bearer %s" % self.config.app_key
        return headers

    def submit(self, draft: Dict[str, Any]) -> Dict[str, Any]:
        """提交一张申报单草稿；返回平台原始应答。"""
        response = json_request(self.http, "POST", self.config.url(), headers=self._headers(), payload=draft, timeout=self.config.timeout)
        body = response.json()
        self.requests.append({
            "url": self.config.url(),
            "method": "POST",
            "body_keys": sorted(draft.keys()),
            "invoice_no": str(draft.get("invoice_no", "")),
            "buyer_tax_no": str(draft.get("buyer_tax_no", "")),
        })
        if not isinstance(body, dict):
            raise EtaxApiError("电子税务局返回的不是对象")
        code = body.get("code", body.get("errcode"))
        if code not in (None, 0, "0"):
            raise EtaxApiError("电子税务局返回 code=%s errmsg=%s" % (code, body.get("errmsg", body.get("message", ""))), payload=body)
        return dict(body)


def submit_with_gate(
    gate: Any,
    client: Optional[EtaxClient],
    draft: Dict[str, Any],
    batch_no: str = "",
) -> Dict[str, Any]:
    """人工核对通过才提交；未确认一律返回 submitted False 且不发任何请求。"""
    from .gates import HumanApprovalRequired

    payload = {
        "batch_no": str(batch_no or draft.get("batch_no", "")),
        "invoice_no": str(draft.get("invoice_no", "")),
        "buyer_tax_no": str(draft.get("buyer_tax_no", "")),
        "amount": str(draft.get("amount", "")),
    }
    try:
        approval = gate.require_human(ACTION_SUBMIT, payload, batch_no=payload["batch_no"])
    except HumanApprovalRequired as exc:
        return {"submitted": False, "reason": "未获人工确认，已阻断提交（载荷摘要 %s）" % exc.digest}
    if client is None:
        return {"submitted": False, "reason": "没有可用的电子税务局客户端，只产出草稿与待提交清单"}
    try:
        body = client.submit(draft)
    except (HttpError, EtaxApiError, EtaxConfigError) as exc:
        return {"submitted": False, "reason": "提交失败：%s" % exc}
    return {
        "submitted": True,
        "submit_no": str(body.get("submit_no", body.get("data", {}).get("submit_no", "")) if isinstance(body.get("data"), dict) else body.get("submit_no", "")),
        "operator": str(getattr(approval, "operator", "")),
        "digest": str(getattr(approval, "digest", "")),
        "at": str(getattr(approval, "at", "")),
    }


def pending_submissions(drafts: Sequence[Dict[str, Any]], reason: str = "") -> List[Dict[str, Any]]:
    """没有凭据时的待提交清单：只列草稿，不假装提交。"""
    out: List[Dict[str, Any]] = []
    for draft in drafts:
        out.append({
            "batch_no": str(draft.get("batch_no", "")),
            "invoice_no": str(draft.get("invoice_no", "")),
            "buyer_tax_no": str(draft.get("buyer_tax_no", "")),
            "amount": str(draft.get("amount", "")),
            "items": len(draft.get("items", [])),
            "status": "pending_manual_submit",
            "reason": str(reason or "缺电子税务局凭据，只产出草稿"),
        })
    return out
