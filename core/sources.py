"""数据来源适配层：夹具 / 录制回放 / 真实领星 API / 真实抓取回放，四条路给出同一套数据形状。

- fixture   仓库自带合成夹具，默认来源，完全离线；
- recorded  fixtures/recorded/*.json 的本地合成响应（origin 如实写 synthetic）；
- lingxing  真实领星开放平台，现场发请求，凭据与地址只从环境变量读；
- capture   `scripts/live_capture.py` 落盘的真实响应回放（origin=live，按 manifest.json 如实标注）。

capture 存在的理由：买方要「可核对的真实取数证据」。真实响应一旦落盘，就能离线反复重算、
任何人拿同一个目录都能跑出同一个数字；manifest.json 里的 origin 字段区分 live 与合成，
读的时候强制校验，合成数据不可能被冒充成 live。

台账写入侧同样分两种：内存表（离线自测）与真实钉钉开放平台（写回区域）。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

from . import lingxing_map
from .dingtalk_client import DingTalkClient, DingTalkConfig, DingTalkConfigError
from .http_client import HttpError
from .lingxing_client import LingxingApiClient, LingxingConfig, LingxingConfigError

SOURCE_FIXTURE = "fixture"
SOURCE_RECORDED = "recorded"
SOURCE_LINGXING = "lingxing"
SOURCE_CAPTURE = "capture"
SOURCE_DINGTALK = "dingtalk"

SOURCES = (SOURCE_FIXTURE, SOURCE_RECORDED, SOURCE_LINGXING, SOURCE_CAPTURE, SOURCE_DINGTALK)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECORDED_DIR = os.path.join(ROOT, "fixtures", "recorded")
CAPTURE_DIR = os.path.join(ROOT, "captures", "live")

# capture manifest 里允许出现的取数来源；只有 live 才算真实抓取。
LIVE_ORIGIN = "live"


class SourceError(RuntimeError):
    """来源不可用：配置缺失、录制文件不存在、抓取目录不合规或响应不合法。"""


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
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            return [dict(item) for item in data["data"]]
        if isinstance(data, list):
            return [dict(item) for item in data]
        for key in ("list", "items", "records", "sheets"):
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


def _payload_items(payload: Any) -> List[Dict[str, Any]]:
    """从官方响应体里取记录数组；形状不合规就返回空列表（由上层如实报缺）。"""
    try:
        return _items(payload, "endpoint")
    except SourceError:
        return []


def _collect_from_endpoints(endpoints: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]], List[str]]:
    """把「各官方接口的原始响应体」拼成内部形状；两个来源共用同一段代码。"""
    product_rows: List[Dict[str, Any]] = []
    for payload in endpoints.get("products", []) or []:
        product_rows.extend(_payload_items(payload))
    supplier_rows: List[Dict[str, Any]] = []
    for payload in endpoints.get("suppliers", []) or []:
        supplier_rows.extend(_payload_items(payload))
    shipment_rows: List[Dict[str, Any]] = []
    for payload in endpoints.get("shipments", []) or []:
        shipment_rows.extend(_payload_items(payload))
    box_rows: List[Dict[str, Any]] = []
    for payload in endpoints.get("boxes", []) or []:
        box_rows.extend(_payload_items(payload))
    customs_rows: List[Dict[str, Any]] = []
    for payload in endpoints.get("customs", []) or []:
        customs_rows.extend(_payload_items(payload))
    detail_rows: List[Dict[str, Any]] = []
    for payload in endpoints.get("customs_detail", []) or []:
        detail_rows.extend(_payload_items(payload))

    products, product_notes = lingxing_map.map_products(product_rows, detail_rows)
    suppliers = lingxing_map.map_suppliers(supplier_rows)
    batches, batch_notes = lingxing_map.map_batches(shipment_rows, box_rows)
    notes = list(product_notes) + list(batch_notes)
    if customs_rows:
        notes.append("报关资料列表返回 %d 条（用于取 declarationNo，明细由 batchDetail 补齐）" % len(customs_rows))
    return batches, products, suppliers, notes


def capture_payloads(directory: str) -> Tuple[Dict[str, List[Any]], Dict[str, Any]]:
    """读一个抓取目录：manifest.json + 每个请求的原始响应体。

    manifest 里的 origin 必须是 live，否则直接报错——合成数据不许从这个入口冒充真实抓取。
    """
    folder = os.path.abspath(directory)
    manifest_path = os.path.join(folder, "manifest.json")
    manifest = _read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise SourceError("manifest.json 不是对象：%s" % manifest_path)
    origin = str(manifest.get("origin", "")).strip().lower()
    if origin != LIVE_ORIGIN:
        raise SourceError(
            "抓取目录 %s 的 origin 是 %r，不是 live；本入口只接受真实抓取的响应（合成数据请用 --source recorded）"
            % (folder, manifest.get("origin"))
        )
    # 只有领星业务接口的抓取才能喂给台账流水线；钉钉侧的抓取目录不能拿来算一致率。
    captured_from = str(manifest.get("source", "")).strip().lower()
    if captured_from and captured_from != SOURCE_LINGXING:
        raise SourceError(
            "抓取目录 %s 抓的是 %r，不是领星业务接口；AC#1 只能用 --source lingxing 的抓取目录"
            % (folder, manifest.get("source"))
        )
    if manifest.get("complete") is False:
        raise SourceError(
            "抓取目录 %s 不完整（failure_count=%s）：有端点失败就没法当作一致率证据源，请按 docs/LIVE_EVIDENCE.md 修好失败端点后重抓"
            % (folder, manifest.get("failure_count"))
        )
    if manifest.get("replayable") is False:
        raise SourceError(
            "抓取目录 %s 标了不可回放（replayable=false，例如用了 --skip-shipments）；缺货件就没法生成台账行，请重抓时带上货件区间"
            % folder
        )
    endpoints: Dict[str, List[Any]] = {}
    for item in manifest.get("endpoints", []) or []:
        name = str(item.get("name", "")).strip()
        relative = str(item.get("response_file", "")).strip()
        if not name or not relative:
            continue
        path = os.path.join(folder, relative)
        endpoints.setdefault(name, []).append(_read_json(path))
    if not endpoints:
        raise SourceError("抓取目录 %s 里没有任何响应文件" % folder)
    return endpoints, manifest


def load_capture(directory: Optional[str] = None):
    """回放一次真实抓取：origin 如实取 manifest 的 live。"""
    folder = directory or CAPTURE_DIR
    endpoints, manifest = capture_payloads(folder)
    batches, products, suppliers, notes = _collect_from_endpoints(endpoints)
    provenance = {
        "source": SOURCE_CAPTURE,
        "origin": LIVE_ORIGIN,
        "host": str(manifest.get("host", "")),
        "fetched_at": str(manifest.get("created_at", "")),
        "files": [
            os.path.relpath(os.path.join(folder, str(item.get("response_file", ""))), ROOT).replace(os.sep, "/")
            for item in manifest.get("endpoints", []) or []
            if item.get("response_file")
        ],
        "capture_dir": os.path.relpath(folder, ROOT).replace(os.sep, "/") if folder.startswith(ROOT) else folder,
        "manifest": os.path.relpath(os.path.join(folder, "manifest.json"), ROOT).replace(os.sep, "/"),
        "endpoint_names": sorted(endpoints),
        "mapping_notes": notes,
        "record_counts": {name: len(items) for name, items in sorted(endpoints.items())},
    }
    return (batches, products, suppliers, provenance)


def _live_batch_window() -> Dict[str, Any]:
    """货件列表接口必填 sid 与日期区间；缺了就不猜，让调用方看到明确的缺参错误。"""
    env = os.environ
    return {
        "sid": str(env.get("LINGXING_SID", "")).strip(),
        "start_date": str(env.get("LINGXING_START_DATE", "")).strip(),
        "end_date": str(env.get("LINGXING_END_DATE", "")).strip(),
    }


def load_from_lingxing(client: Optional[LingxingApiClient] = None):
    """真实领星开放平台：鉴权、签名、分页、重试都走 LingxingApiClient。"""
    api = client or LingxingApiClient(LingxingConfig.from_env())
    endpoints: Dict[str, List[Any]] = {}
    endpoints["products"] = api.fetch_all("products")
    endpoints["suppliers"] = api.fetch_all("suppliers")
    window = _live_batch_window()
    if window["sid"] and window["start_date"] and window["end_date"]:
        endpoints["shipments"] = [api.fetch_all("shipments", extra_body=window)]
    else:
        raise SourceError(
            "货件列表接口必填 sid 与日期区间：请设置 LINGXING_SID / LINGXING_START_DATE / LINGXING_END_DATE"
        )
    customs_rows = api.fetch_all("customs", extra_body={"length": 200})
    endpoints["customs"] = [customs_rows]
    numbers = [str(item.get("declarationNo", "")).strip() for item in customs_rows if item.get("declarationNo")]
    if numbers:
        endpoints["customs_detail"] = [api.batch_customs_detail(numbers)]
    batches, products, suppliers, notes = _collect_from_endpoints(endpoints)
    provenance = {
        "source": SOURCE_LINGXING,
        "origin": LIVE_ORIGIN,
        "host": api.config.base_url,
        "files": [str(api.config.base_url)],
        "endpoint_names": sorted(endpoints),
        "mapping_notes": notes,
        "requests": len(api.requests),
    }
    return (batches, products, suppliers, provenance)


def load_source(
    source: str,
    fixtures_dir: Optional[str] = None,
    recorded_dir: Optional[str] = None,
    client: Optional[LingxingApiClient] = None,
    capture_dir: Optional[str] = None,
):
    """按来源名取数；所有来源给出同一套 (batches, products, suppliers, provenance)。"""
    name = str(source or SOURCE_FIXTURE).strip().lower()
    if name == SOURCE_FIXTURE:
        from .pipeline import load_fixtures

        folder = fixtures_dir or os.path.join(ROOT, "fixtures", "lingxing")
        batches, products, suppliers = load_fixtures(folder)
        provenance = {
            "source": SOURCE_FIXTURE,
            "origin": "fixture",
            "files": ["fixtures/lingxing/batches.json", "fixtures/lingxing/products.json", "fixtures/lingxing/suppliers.json"],
        }
        return batches, products, suppliers, provenance
    if name == SOURCE_RECORDED:
        return load_recorded(recorded_dir)
    if name == SOURCE_CAPTURE:
        return load_capture(capture_dir)
    if name == SOURCE_LINGXING:
        try:
            return load_from_lingxing(client)
        except (LingxingConfigError, HttpError, SourceError) as exc:
            raise SourceError(str(exc))
    if name == SOURCE_DINGTALK:
        """钉钉模式：ERP 侧读本地夹具，台账侧直连真实钉钉开放平台（读 + 写）。"""
        from .pipeline import load_fixtures

        folder = fixtures_dir or os.path.join(ROOT, "fixtures", "lingxing")
        batches, products, suppliers = load_fixtures(folder)
        provenance = {
            "source": SOURCE_DINGTALK,
            "origin": "fixture",
            "files": ["fixtures/lingxing/batches.json", "fixtures/lingxing/products.json", "fixtures/lingxing/suppliers.json"],
        }
        return batches, products, suppliers, provenance
    raise SourceError("unknown source %r, expected one of %s" % (source, ", ".join(SOURCES)))


class SheetSink:
    """台账写回目标：默认内存表（离线自测），真实模式下发 HTTP 请求写回钉钉区域。"""

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

