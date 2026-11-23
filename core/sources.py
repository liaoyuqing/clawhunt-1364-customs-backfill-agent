"""数据来源适配层：夹具 / 录制回放 / 真实领星 API，三条路给出同一套数据形状。

- fixture   仓库自带合成夹具，默认来源，完全离线；
- recorded  fixtures/recorded/*.json 的本地合成响应，用于证明调用路径；
- lingxing  真实领星开放平台，凭据与地址只从环境变量读。

台账写入侧同样分两种：内存表（离线自测）与真实钉钉开放平台（写回行）。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

from .dingtalk_client import DingTalkClient, DingTalkConfig, DingTalkConfigError
from .http_client import HttpError
from .lingxing_client import LingxingApiClient, LingxingConfig, LingxingConfigError

SOURCE_FIXTURE = "fixture"
SOURCE_RECORDED = "recorded"
SOURCE_LINGXING = "lingxing"
SOURCE_DINGTALK = "dingtalk"

SOURCES = (SOURCE_FIXTURE, SOURCE_RECORDED, SOURCE_LINGXING, SOURCE_DINGTALK)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECORDED_DIR = os.path.join(ROOT, "fixtures", "recorded")


class SourceError(RuntimeError):
    """来源不可用：配置缺失、录制文件不存在或响应不合法。"""


def _read_json(path: str) -> Any:
    if not os.path.exists(path):
        raise SourceError("file not found: %s" % path)
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        raise SourceError("not valid JSON: %s (%s)" % (path, exc))


def _items(payload: Any, label: str) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(item) for item in payload]
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("list"), list):
            return [dict(item) for item in data["list"]]
        if isinstance(data, list):
            return [dict(item) for item in data]
        for key in ("list", "items", "records"):
            if isinstance(payload.get(key), list):
                return [dict(item) for item in payload[key]]
    raise SourceError("%s payload has no list of records" % label)


def load_recorded(directory: Optional[str] = None):
    """读 fixtures/recorded/ 下的录制响应（本地合成）。"""
    folder = directory or RECORDED_DIR
    batches = _items(_read_json(os.path.join(folder, "lingxing_batches_page1.SYNTHETIC.json")), "batches")
    products = _items(_read_json(os.path.join(folder, "lingxing_products_page1.SYNTHETIC.json")), "products")
    suppliers = _items(_read_json(os.path.join(folder, "lingxing_suppliers_page1.SYNTHETIC.json")), "suppliers")
    provenance = {
        "source": SOURCE_RECORDED,
        "origin": "local synthetic replay, not a capture from the vendor API",
        "files": [
            "fixtures/recorded/lingxing_batches_page1.SYNTHETIC.json",
            "fixtures/recorded/lingxing_products_page1.SYNTHETIC.json",
            "fixtures/recorded/lingxing_suppliers_page1.SYNTHETIC.json",
        ],
    }
    return (batches, {str(item.get("sku")): item for item in products}, {str(item.get("id")): item for item in suppliers}, provenance)


def load_from_lingxing(client: Optional[LingxingApiClient] = None):
    """真实领星开放平台：鉴权、签名、分页、重试都走 LingxingApiClient。"""
    api = client or LingxingApiClient(LingxingConfig.from_env())
    batches = api.fetch_all("batches")
    products = api.fetch_all("products")
    suppliers = api.fetch_all("suppliers")
    request_count = len(api.transport.requests) if hasattr(api.transport, "requests") else 0
    provenance = {
        "source": SOURCE_LINGXING,
        "origin": "lingxing open api",
        "base_url": api.config.base_url,
        "requests": request_count,
    }
    return (batches, {str(item.get("sku")): item for item in products}, {str(item.get("id")): item for item in suppliers}, provenance)


def load_source(source: str, fixtures_dir: Optional[str] = None, recorded_dir: Optional[str] = None, client: Optional[LingxingApiClient] = None):
    """按来源名取数；三种来源给出同一套 (batches, products, suppliers, provenance)。"""
    name = str(source or SOURCE_FIXTURE).strip().lower()
    if name == SOURCE_FIXTURE:
        from .pipeline import load_fixtures

        folder = fixtures_dir or os.path.join(ROOT, "fixtures", "lingxing")
        batches, products, suppliers = load_fixtures(folder)
        provenance = {
            "source": SOURCE_FIXTURE,
            "origin": "synthetic fixtures in this repository",
            "files": ["fixtures/lingxing/batches.json", "fixtures/lingxing/products.json", "fixtures/lingxing/suppliers.json"],
        }
        return batches, products, suppliers, provenance
    if name == SOURCE_RECORDED:
        return load_recorded(recorded_dir)
    if name == SOURCE_LINGXING:
        try:
            return load_from_lingxing(client)
        except (LingxingConfigError, HttpError) as exc:
            raise SourceError(str(exc))
    if name == SOURCE_DINGTALK:
        """钉钉模式：ERP 侧读本地夹具，台账侧直连真实钉钉开放平台（读 + 写）。"""
        from .pipeline import load_fixtures

        folder = fixtures_dir or os.path.join(ROOT, "fixtures", "lingxing")
        batches, products, suppliers = load_fixtures(folder)
        provenance = {
            "source": SOURCE_DINGTALK,
            "origin": "erp data from local fixtures; ledger read/write goes to the real dingtalk open platform",
            "files": ["fixtures/lingxing/batches.json", "fixtures/lingxing/products.json", "fixtures/lingxing/suppliers.json"],
        }
        return batches, products, suppliers, provenance
    raise SourceError("unknown source %r, expected one of %s" % (source, ", ".join(SOURCES)))


class SheetSink:
    """台账写回目标：默认内存表（离线自测），真实模式下发 HTTP 请求写回钉钉。"""

    def __init__(self, client: Optional[DingTalkClient] = None) -> None:
        self.client = client
        self.written: List[Dict[str, Any]] = []

    @property
    def live(self) -> bool:
        return self.client is not None

    def read_rows(self) -> List[Dict[str, Any]]:
        """读回目标表现有行；内存模式返回空列表。"""
        if self.client is None:
            return []
        try:
            return list(self.client.read_rows())
        except (DingTalkConfigError, HttpError) as exc:
            raise SourceError(str(exc))

    def write_row(self, row_key: str, values: Dict[str, Any]) -> Dict[str, Any]:
        """写一行；真实模式发 HTTP 请求，内存模式只记一笔。"""
        if self.client is None:
            self.written.append({"row_key": row_key, "mode": "memory"})
            return {"row_key": row_key, "mode": "memory"}
        try:
            answer = self.client.write_row(row_key, values)
        except (DingTalkConfigError, HttpError) as exc:
            raise SourceError(str(exc))
        self.written.append({"row_key": row_key, "mode": "dingtalk", "answer": answer})
        return answer


def sheet_sink_from_env(env: Optional[Dict[str, str]] = None) -> SheetSink:
    """从环境变量造真实钉钉客户端；配置不全时抛错，绝不假装写成功。"""
    try:
        config = DingTalkConfig.from_env(env)
    except DingTalkConfigError as exc:
        raise SourceError(str(exc))
    return SheetSink(DingTalkClient(config))