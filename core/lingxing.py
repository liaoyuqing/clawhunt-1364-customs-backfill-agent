"""领星 ERP 适配层：分页、限流退避、字段映射。真接口与录像桩同一个接口。"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional


class LingxingError(RuntimeError):
    """接口返回非 0 或重试耗尽。"""


class RateLimited(LingxingError):
    """429：带 Retry-After 的限流。"""


def _noop_sleep(_seconds: float) -> None:
    return None


class LingxingClient:
    """transport(page, size) -> {"code":0,"data":{"list":[...],"total":N}}。"""

    def __init__(
        self,
        transport: Callable[[str, int, int], Dict[str, Any]],
        page_size: int = 100,
        max_retries: int = 4,
        sleep: Callable[[float], None] = _noop_sleep,
        retry_after_default: float = 1.0,
    ) -> None:
        self._transport = transport
        self.page_size = page_size
        self.max_retries = max_retries
        self._sleep = sleep
        self._retry_after_default = retry_after_default
        self.calls: List[str] = []
        self.retries: List[Dict[str, Any]] = []

    def fetch_all(self, resource: str) -> List[Dict[str, Any]]:
        """按 offset 翻页直到取满 total，重复页直接判定异常。"""
        out: List[Dict[str, Any]] = []
        offset = 0
        seen = set()
        while True:
            page = self._call(resource, offset, self.page_size)
            items = page.get("list", [])
            total = int(page.get("total", 0))
            key = tuple(str(item.get("id")) for item in items)
            if items and key in seen:
                raise LingxingError("pagination returned a repeated page for %s at offset %s" % (resource, offset))
            seen.add(key)
            out.extend(items)
            offset += self.page_size
            if not items or len(out) >= total:
                break
        return out

    def _call(self, resource: str, offset: int, size: int) -> Dict[str, Any]:
        attempt = 0
        while True:
            self.calls.append("%s@%s" % (resource, offset))
            try:
                payload = self._transport(resource, offset, size)
            except RateLimited as exc:
                wait = getattr(exc, "retry_after", None) or self._retry_after_default
                if attempt >= self.max_retries:
                    raise
                self.retries.append({"resource": resource, "offset": offset, "attempt": attempt, "wait": wait})
                self._sleep(float(wait))
                attempt += 1
                continue
            if int(payload.get("code", 0)) != 0:
                raise LingxingError("lingxing returned code=%s msg=%s" % (payload.get("code"), payload.get("msg")))
            return dict(payload.get("data") or {})


def product_declare_fields(item: Dict[str, Any]) -> Dict[str, str]:
    """把领星产品记录映射成台账列（中文报关名/材质/用途/单位/货源地/HSCODE/型号）。"""
    return {
        "产品中文报关名": str(item.get("declare_name", "")).strip(),
        "型号": str(item.get("model", "")).strip(),
        "材质": str(item.get("material", "")).strip(),
        "用途": str(item.get("usage", "")).strip(),
        "报关单位": str(item.get("declare_unit", "")).strip(),
        "境内货源地": str(item.get("origin_place", "")).strip(),
        "HSCODE": str(item.get("hscode", "")).strip(),
        "SKU": str(item.get("sku", "")).strip(),
    }


class FixtureTransport:
    """录像桩：从 fixtures/lingxing/*.json 回放，供无授权时全链路可跑。"""

    def __init__(self, pages: Dict[str, List[List[Dict[str, Any]]]], fail_plan: Optional[Dict[str, int]] = None) -> None:
        self._pages = pages
        self._fail_plan = dict(fail_plan or {})
        self.calls: List[str] = []

    def __call__(self, resource: str, offset: int, size: int) -> Dict[str, Any]:
        self.calls.append("%s@%s" % (resource, offset))
        plan_key = "%s@%s" % (resource, offset)
        if plan_key in self._fail_plan and self._fail_plan[plan_key] > 0:
            self._fail_plan[plan_key] -= 1
            exc = RateLimited("429")
            exc.retry_after = 2
            raise exc
        groups = self._pages.get(resource, [])
        index = offset // size
        items = groups[index] if index < len(groups) else []
        total = sum(len(group) for group in groups)
        return {"code": 0, "data": {"list": items, "total": total}}