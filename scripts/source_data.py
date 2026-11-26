"""取数层：夹具 / 录制回放 / 真实抓取回放 / 真实领星接口，四种来源给出同一套数据形状。

sample50.py 与 reference_impl.py 都从这里取数，避免两套取数逻辑。
来源信息随数据一起返回，且**一定**是下列三个值之一，不允许模糊表述：

    live      真实账号、真实接口取得（--source lingxing 现场发请求，或 --source capture 回放真实抓取）
    recorded  本地合成的录制响应（fixtures/recorded/*.SYNTHETIC.json）
    fixture   仓库自带合成夹具（fixtures/lingxing/*.json）

原来的 origin 是一句描述性英文；买方第 2 次驳回指出「合成数据自比不能当成真实源证据」，
所以现在把 origin 收紧成枚举，描述性文字挪到 source_origin_detail，报告里两个字段都在。
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.sources import (  # noqa: E402
    CAPTURE_DIR,
    RECORDED_DIR,
    SOURCE_CAPTURE,
    SOURCE_FIXTURE,
    SOURCE_LINGXING,
    SOURCE_RECORDED,
    SourceError,
    load_source,
)

FIXTURES_DIR = os.path.join(ROOT, "fixtures", "lingxing")
INVOICE_LEDGER = os.path.join(ROOT, "fixtures", "invoices", "ledger.json")

# 真实接口必需的变量名。base_url 也要求显式配置：宁可报错，也不要默默指向生产地址。
REQUIRED_ENV = (
    "LINGXING_BASE_URL",
    "LINGXING_APP_ID",
    "LINGXING_APP_SECRET",
)

ORIGIN_LIVE = "live"
ORIGIN_RECORDED = "recorded"
ORIGIN_FIXTURE = "fixture"


class SourceDataError(RuntimeError):
    """数据源不可用：配置缺失、录制文件不全或抓取目录不合规。"""


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_invoice_records(path: str = INVOICE_LEDGER) -> list:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _describe_origin(name: str, provenance: Dict[str, Any]) -> str:
    """来源枚举；live 只在真的从平台取到数据时才会出现。"""
    if name == SOURCE_FIXTURE:
        return ORIGIN_FIXTURE
    if name == SOURCE_RECORDED:
        return ORIGIN_RECORDED
    if name in (SOURCE_LINGXING, SOURCE_CAPTURE):
        # 走真实路径的两种来源：provenance.origin 必须自报 live，否则视为来源不明。
        declared = str(provenance.get("origin", "")).strip().lower()
        if declared != ORIGIN_LIVE:
            raise SourceDataError(
                "来源 %s 拿到的 provenance.origin 是 %r，不是 live；拒绝把未知口径当成真实源"
                % (name, provenance.get("origin"))
            )
        return ORIGIN_LIVE
    raise SourceDataError("未知数据来源 %r，可用：fixture / recorded / capture / lingxing" % name)


def load_source_data(
    source: str,
    fixtures_dir: Optional[str] = None,
    recorded_dir: Optional[str] = None,
    capture_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """取一份数据，连同来源信息一起返回。

    返回 {batches, products, suppliers, invoices, provenance}；provenance 里
    source/origin/files/fetched_at/host/label/synthetic 说明这份数据从哪来、什么时候取的、
    是不是合成口径。origin 只能是 live / recorded / fixture。
    """
    name = str(source or SOURCE_FIXTURE).strip().lower()
    if name == SOURCE_FIXTURE:
        folder = fixtures_dir or FIXTURES_DIR
        batches, products, suppliers, provenance = load_source(SOURCE_FIXTURE, fixtures_dir=folder)
        detail = "synthetic fixtures in this repository"
        files = list(provenance.get("files", []))
        synthetic = True
    elif name == SOURCE_RECORDED:
        folder = recorded_dir or RECORDED_DIR
        batches, products, suppliers, provenance = load_source(SOURCE_RECORDED, recorded_dir=folder)
        detail = str(provenance.get("origin", ""))
        files = list(provenance.get("files", []))
        write_response = os.path.join(folder, "dingtalk_write_response.json")
        if not os.path.exists(write_response):
            raise SourceDataError("录制响应不完整：%s 不存在" % write_response)
        files.append("fixtures/recorded/dingtalk_write_response.json")
        synthetic = True
    elif name == SOURCE_CAPTURE:
        folder = capture_dir or CAPTURE_DIR
        try:
            batches, products, suppliers, provenance = load_source(SOURCE_CAPTURE, capture_dir=folder)
        except SourceError as exc:
            raise SourceDataError(str(exc))
        detail = "replay of a real capture produced by scripts/live_capture.py (manifest.json origin=live)"
        files = list(provenance.get("files", []))
        synthetic = False
    elif name == SOURCE_LINGXING:
        missing = [key for key in REQUIRED_ENV if not str(os.environ.get(key, "")).strip()]
        if missing:
            raise SourceDataError(
                "真实领星接口缺少环境变量：%s；配齐后再跑 --source lingxing" % "、".join(missing)
            )
        try:
            batches, products, suppliers, provenance = load_source(SOURCE_LINGXING)
        except SourceError as exc:
            raise SourceDataError(str(exc))
        detail = "lingxing open api, live requests issued by this process"
        files = [str(provenance.get("base_url") or provenance.get("host") or "")]
        synthetic = False
    else:
        raise SourceDataError("未知数据来源 %r，可用：fixture / recorded / capture / lingxing" % source)

    origin = _describe_origin(name, provenance)
    fetched_at = str(provenance.get("fetched_at") or "") or _now()
    host = str(provenance.get("host") or provenance.get("base_url") or "") or (
        "local files (no network)" if origin != ORIGIN_LIVE else ""
    )
    label = "source=%s%s 取数时间 %s" % (
        name,
        "(synthetic)" if synthetic else "",
        fetched_at,
    )
    provenance = dict(provenance)
    provenance.update({
        "source": name,
        "origin": origin,
        "origin_detail": detail,
        "host": host,
        "files": files,
        "fetched_at": fetched_at,
        "label": label,
        "synthetic": synthetic,
    })
    return {
        "batches": batches,
        "products": products,
        "suppliers": suppliers,
        "invoices": load_invoice_records(),
        "provenance": provenance,
    }

