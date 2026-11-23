"""冒烟脚本：验证「真的发起了平台调用」这一层，而不是只验证本地函数。

用法：
    python scripts/recorded_smoke.py --source recorded   # 本地合成回放，默认
    python scripts/recorded_smoke.py --source live      # 打真实领星 / 钉钉接口

live 模式从环境变量读地址与凭据，仓库里不含任何凭据：
    LINGXING_BASE_URL / LINGXING_APP_KEY / LINGXING_APP_SECRET / LINGXING_ACCESS_TOKEN
    DINGTALK_BASE_URL / DINGTALK_APP_KEY / DINGTALK_APP_SECRET / DINGTALK_SHEET_ID

断言两件事：
    1. 能从来源拉到一个商品的报关字段（品名 / 型号 / 材质 / 用途 / 货源地 / HSCODE / 单位）；
    2. 能把一行台账写回目标表，并读回平台应答。

以下为本地合成的可重放响应，用于证明调用路径与解析逻辑正确；接贵方账号后用
scripts/recorded_smoke.py --source live 拉真实数据做同样断言。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.dingtalk_client import DingTalkClient, DingTalkConfig, DingTalkConfigError  # noqa: E402
from core.http_client import HttpRequest, HttpResponse, RecordingTransport  # noqa: E402
from core.lingxing import product_declare_fields  # noqa: E402
from core.lingxing_client import (  # noqa: E402
    LingxingApiClient,
    LingxingConfig,
    LingxingConfigError,
)
from core.sources import load_recorded  # noqa: E402

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_CONFIG = 2


def _fail(message):
    print("冒烟失败：%s" % message, file=sys.stderr)
    return EXIT_FAIL


def _dingtalk_response():
    """录制回放用的钉钉应答（本地合成，不是平台真实抓包）。"""
    body = json.dumps({
        "errcode": 0,
        "errmsg": "ok",
        "sheetId": "SHEET-SYNTHETIC-NOT-REAL",
        "rowId": "ROW-SYNTHETIC-0001",
    }).encode("utf-8")
    return HttpResponse(200, {"Content-Type": "application/json"}, body)


class _RecordedTransport:
    """把标准 HTTP 请求翻译成领星分页响应形状；请求本身照常被记录下来。"""

    def __init__(self, records_by_resource):
        self.records_by_resource = records_by_resource
        self.requests = []

    def __call__(self, resource, offset, size, timestamp, nonce):
        url = "https://lingxing.example.invalid/openapi/v1/%s?offset=%d&page_size=%d" % (resource, offset, size)
        request = HttpRequest(
            method="GET",
            url=url,
            headers={"app-key": "TEST-ONLY-NOT-REAL", "access-token": "TEST-ONLY-NOT-REAL"},
        )
        self.requests.append(request.describe())
        records = list(self.records_by_resource.get(resource, []))
        page = records[offset:offset + size]
        return {
            "code": 0,
            "msg": "synthetic replay",
            "data": {"list": page, "total": len(records)},
        }


def _recorded_client():
    """用本地合成的录制响应造一个领星客户端，走的是同一个 HTTP 调用路径。"""
    try:
        batches, products, suppliers, _provenance = load_recorded()
    except Exception as exc:
        raise RuntimeError("录制响应不可用：%s" % exc)
    client = LingxingApiClient(
        LingxingConfig(
            base_url="https://lingxing.example.invalid",
            app_key="TEST-ONLY-NOT-REAL",
            app_secret="TEST-ONLY-NOT-REAL",
            access_token="TEST-ONLY-NOT-REAL",
            page_size=100,
        ),
        transport=_RecordedTransport(
            {
                "batches": batches,
                "products": list(products.values()),
                "suppliers": list(suppliers.values()),
            }
        ),
        sleep=lambda seconds: None,
    )
    return client


def _live_client():
    """真实领星客户端：地址与凭据只从环境变量读。"""
    try:
        config = LingxingConfig.from_env()
    except LingxingConfigError as exc:
        raise RuntimeError(str(exc))
    return LingxingApiClient(config)


def _write_row_live(row_key, values):
    """真实钉钉写回。"""
    try:
        config = DingTalkConfig.from_env()
    except DingTalkConfigError as exc:
        raise RuntimeError(str(exc))
    client = DingTalkClient(config)
    answer = client.write_row(row_key, values)
    return answer, client.requests


def _write_row_recorded(row_key, values):
    """录制回放：同样发出一条真实形态的 HTTP 请求，只是应答来自本地合成。"""
    config = DingTalkConfig(
        base_url="https://dingtalk.example.invalid",
        app_key="TEST-ONLY-NOT-REAL",
        app_secret="TEST-ONLY-NOT-REAL",
        sheet_id="SHEET-SYNTHETIC-NOT-REAL",
    )
    write_transport = RecordingTransport([_dingtalk_response()])
    client = DingTalkClient(config, http=write_transport, sleep=lambda seconds: None)
    """令牌用明显可辨的示意形态，不是平台真实令牌。"""
    client._token = "TEST-ONLY-NOT-REAL"
    answer = client.write_row(row_key, values)
    return answer, client.requests


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python scripts/recorded_smoke.py")
    parser.add_argument("--source", default="recorded", choices=["recorded", "live"], help="recorded=本地合成回放；live=真实接口")
    parser.add_argument("--batch", default="B2026-0001", help="写回台账时用的行键，默认 B2026-0001")
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        client = _live_client() if args.source == "live" else _recorded_client()
    except RuntimeError as exc:
        print("冒烟无法开始：%s" % exc, file=sys.stderr)
        return EXIT_CONFIG

    try:
        items = client.fetch_all("products")
    except Exception as exc:
        return _fail("拉取商品失败：%s" % exc)
    if not items:
        return _fail("来源里没有商品记录")

    fields = product_declare_fields(items[0])
    missing = [key for key, value in fields.items() if not str(value).strip()]
    if missing:
        return _fail("商品报关字段不全，缺 %s" % "、".join(missing))

    print("products_ok %d sku=%s name=%s hscode=%s" % (len(items), fields["SKU"], fields["产品中文报关名"], fields["HSCODE"]))

    values = {
        "批次号": args.batch,
        "SKU": fields["SKU"],
        "产品中文报关名": fields["产品中文报关名"],
        "HSCODE": fields["HSCODE"],
    }
    try:
        if args.source == "live":
            answer, requests = _write_row_live(args.batch, values)
        else:
            answer, requests = _write_row_recorded(args.batch, values)
    except Exception as exc:
        return _fail("写回台账失败：%s" % exc)

    for item in requests:
        print("request %s %s headers=%s body=%s" % (item.get("method"), item.get("url"), json.dumps(item.get("headers"), ensure_ascii=False), item.get("body_keys")))
    print("write_ok sheetId=%s rowId=%s" % (answer.get("sheetId", ""), answer.get("rowId", "")))
    print("冒烟通过：取商品字段 + 写回台账都走到了 HTTP 调用这一层（来源 %s）" % args.source)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
