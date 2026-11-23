"""取数层：夹具 / 录制回放 / 真实领星接口，三种来源给出同一套数据形状。

sample50.py 与 reference_impl.py 都从这里取数，避免两套取数逻辑。
来源信息随数据一起返回：文件名或接口地址、取数时间、以及一句话口径标注。
合成自比（fixture / recorded）会被显式标注为 synthetic，避免与真实源口径混淆。
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
    RECORDED_DIR,
    SOURCE_FIXTURE,
    SOURCE_LINGXING,
    SOURCE_RECORDED,
    SourceError,
    load_source,
)

FIXTURES_DIR = os.path.join(ROOT, "fixtures", "lingxing")
INVOICE_LEDGER = os.path.join(ROOT, "fixtures", "invoices", "ledger.json")

REQUIRED_ENV = (
    "LINGXING_BASE_URL",
    "LINGXING_APP_KEY",
    "LINGXING_APP_SECRET",
)


class SourceDataError(RuntimeError):
    """数据源不可用：配置缺失或录制文件不全。"""


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_invoice_records(path: str = INVOICE_LEDGER) -> list:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def load_source_data(source: str, fixtures_dir: Optional[str] = None, recorded_dir: Optional[str] = None) -> Dict[str, Any]:
    """取一份数据，连同来源信息一起返回。

    返回 {batches, products, suppliers, invoices, provenance}；provenance 里 source/origin/
    files/fetched_at/label 说明这份数据从哪来、什么时候取的、是不是合成口径。
    """
    name = str(source or SOURCE_FIXTURE).strip().lower()
    if name == SOURCE_FIXTURE:
        folder = fixtures_dir or FIXTURES_DIR
        batches, products, suppliers, provenance = load_source(SOURCE_FIXTURE, fixtures_dir=folder)
        origin = "synthetic fixtures in this repository"
        files = list(provenance.get("files", []))
        synthetic = True
    elif name == SOURCE_RECORDED:
        folder = recorded_dir or RECORDED_DIR
        batches, products, suppliers, provenance = load_source(SOURCE_RECORDED, recorded_dir=folder)
        origin = str(provenance.get("origin", ""))
        files = list(provenance.get("files", []))
        write_response = os.path.join(folder, "dingtalk_write_response.json")
        if not os.path.exists(write_response):
            raise SourceDataError("录制响应不完整：%s 不存在" % write_response)
        files.append("fixtures/recorded/dingtalk_write_response.json")
        synthetic = True
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
        origin = str(provenance.get("origin", ""))
        files = [str(provenance.get("base_url", ""))]
        synthetic = False
    else:
        raise SourceDataError("未知数据来源 %r，可用：fixture / recorded / lingxing" % source)

    fetched_at = _now()
    label = "source=%s%s 取数时间 %s" % (
        name,
        "(synthetic)" if synthetic else "",
        fetched_at,
    )
    provenance = dict(provenance)
    provenance.update({
        "source": name,
        "origin": origin,
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
