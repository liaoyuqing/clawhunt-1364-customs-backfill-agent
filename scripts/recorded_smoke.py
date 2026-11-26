"""冒烟脚本：验证「真的发起了平台调用」这一层，而不是只验证本地函数。

用法（PowerShell）：
    python scripts/recorded_smoke.py --source recorded   # 本地合成回放，默认，完全离线
    python scripts/recorded_smoke.py --source live       # 打真实领星 / 钉钉接口

live 模式从环境变量读地址与凭据，仓库里不含任何凭据：
    领星 v2  LINGXING_BASE_URL / LINGXING_APP_ID / LINGXING_APP_SECRET
    钉钉     DINGTALK_BASE_URL / DINGTALK_APP_KEY（或 DINGTALK_CLIENT_ID）/ DINGTALK_APP_SECRET、
             DINGTALK_WORKBOOK_ID / DINGTALK_SHEET_ID / DINGTALK_OPERATOR_ID

断言两件事：
    1. 能从来源拉到一个商品的报关字段（品名 / 型号 / 材质 / 用途 / 货源地 / HSCODE / 单位）；
    2. 能把一行台账写回目标表，并读回平台应答。

两点与上一轮的差别，值得说明：
  · 领星换成了官方 v2 协议：POST + JSON 体、签名走 Query（MD5+AES）、token 走 Authorization。
    所以本地回放也必须走**同一个客户端**，只把传输层换成回放器，否则验的不是同一条路。
  · 商品报关字段在真实接口里不在产品列表，而在报关资料明细（customsDeclaration/batchDetail）。
    因此 live 模式若只配了产品接口，会明确报「报关字段缺失」而不是假装通过。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.parse import urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import lingxing_map  # noqa: E402
from core.dingtalk_client import DingTalkClient, DingTalkConfig, DingTalkConfigError  # noqa: E402
from core.http_client import HttpError, HttpResponse, RecordingTransport  # noqa: E402
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

# 回放用的假域名：明显不是生产地址，避免被误当成真实调用。
REPLAY_HOST = "https://lingxing.example.invalid"
DINGTALK_REPLAY_HOST = "https://dingtalk.example.invalid"


def _fail(message):
    print("冒烟失败：%s" % message, file=sys.stderr)
    return EXIT_FAIL


def _json_response(payload, status=200):
    return HttpResponse(status, {"Content-Type": "application/json"},
                        json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _declaration_item(recorded_product, sku):
    """把录制夹具里的内部字段拼成官方报关资料明细里的 products[] 元素。

    这样回放走的是与真实接口**同一套映射代码**（core.lingxing_map），
    而不是绕过映射直接喂内部形状。
    """
    return {
        "sku": sku,
        "customsNameCn": str(recorded_product.get("declare_name", "")),
        "model": str(recorded_product.get("model", "")),
        "materialCn": str(recorded_product.get("material", "")),
        "useCn": str(recorded_product.get("usage", "")),
        "transactionUnit": str(recorded_product.get("declare_unit", "")),
        "domesticSource": str(recorded_product.get("origin_place", "")),
        "productCode": str(recorded_product.get("hscode", "")),
        "category": str(recorded_product.get("category", "")),
    }


class _ReplayTransport:
    """把请求翻译成官方形状的合成响应；请求本身照常被记录下来（URL/头/体都在）。

    产品列表接口与报关资料明细接口给的是**两套字段**，与真实平台一致：
    列表接口只有 sku / product_name / cg_price / category_name，
    报关字段（中文报关名、型号、材质、用途、报关单位、境内货源地、HSCODE）在 batchDetail 里。
    这样回放走的是与真实接口同一套映射（core.lingxing_map），不是绕过映射直接喂内部形状。
    """

    def __init__(self, declared_products, suppliers):
        self.declared_products = dict(declared_products)
        self.products = [
            {
                "id": int(index),
                "sku": key,
                "product_name": value.get("declare_name", ""),
                "cg_price": str(value.get("purchase_price", "0")),
                "category_name": value.get("category", ""),
            }
            for index, (key, value) in enumerate(sorted(self.declared_products.items()), start=1)
        ]
        self.suppliers = list(suppliers)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        path = urlsplit(request.url).path
        if path.endswith("/api/auth-server/oauth/access-token"):
            return _json_response({"code": "200", "msg": "OK",
                                   "data": {"access_token": "TOKEN-SYNTHETIC-NOT-REAL", "expires_in": 7199}})
        if path.endswith("/local_inventory/productList"):
            return _json_response({"code": 0, "message": "success", "total": len(self.products), "data": self.products})
        if path.endswith("/local_inventory/supplier"):
            return _json_response({"code": 0, "message": "success", "total": len(self.suppliers), "data": self.suppliers})
        if path.endswith("/customsDeclaration/batchDetail"):
            detail = [{
                "declarationNo": "DECL-SYNTHETIC-0001",
                "products": [_declaration_item(value, key) for key, value in sorted(self.declared_products.items())],
            }]
            return _json_response({"code": 0, "message": "success", "data": detail})
        if path.endswith("/customs/search"):
            return _json_response({"code": 0, "message": "success",
                                   "data": {"list": [{"declarationNo": "DECL-SYNTHETIC-0001"}], "total": 1}})
        raise HttpError("回放器没有这个端点的合成响应：%s %s" % (request.method, path))


def _recorded_client():
    """用本地合成的录制响应造一个领星客户端，走的是同一个 HTTP 调用路径。"""
    try:
        _batches, products, suppliers, _provenance = load_recorded()
    except Exception as exc:
        raise RuntimeError("录制响应不可用：%s" % exc)
    config = LingxingConfig(
        base_url=REPLAY_HOST,
        app_id="SYNTHETIC-APP-16",  # 16 字节（AES 密钥长度要求），明显是示意值
        app_secret="SECRET-SYNTHETIC-NOT-REAL",
        page_size=100,
    )
    transport = _ReplayTransport(
        products,
        [{"supplier_id": key, "supplier_name": value.get("name", ""),
          "contact_person": value.get("contact", ""), "credit_code": value.get("tax_no", "")}
         for key, value in sorted(suppliers.items())],
    )
    return LingxingApiClient(config, http=transport, sleep=lambda seconds: None), transport


def _live_client():
    """真实领星客户端：地址与凭据只从环境变量读。"""
    try:
        config = LingxingConfig.from_env()
        config.require_credentials()
    except LingxingConfigError as exc:
        raise RuntimeError(str(exc))
    return LingxingApiClient(config), None


def _declared_records(client):
    """报关明细：先取报关资料列表拿 declarationNo，再批量取明细（官方两段式）。"""
    listing = client.fetch_all("customs", extra_body={"length": 200})
    numbers = [str(item.get("declarationNo", "")).strip() for item in listing if item.get("declarationNo")]
    if not numbers:
        return []
    return client.batch_customs_detail(numbers)


def _write_row_live(row_key, values):
    """真实钉钉写回（官方 workbooks/sheets/ranges，PUT + operatorId）。"""
    try:
        config = DingTalkConfig.from_env()
        config.require_credentials()
        config.require_target()
    except DingTalkConfigError as exc:
        raise RuntimeError(str(exc))
    client = DingTalkClient(config)
    answer = client.write_row(row_key, values)
    return answer, client.requests


def _write_row_recorded(row_key, values):
    """录制回放：同样走真实的 write_row 逻辑（读表头、定位行号、PUT 单行），应答来自本地合成。"""
    header = [[name for name in values.keys()]]
    existing = [list(values.keys()), ["B2026-0000"] + [""] * (len(values) - 1)]
    responses = [
        _json_response({"values": header}),
        _json_response({"values": existing}),
        _json_response({"values": [list(values.values())]}),
    ]
    config = DingTalkConfig(
        base_url=DINGTALK_REPLAY_HOST,
        app_key="APP-SYNTHETIC-NOT-REAL",
        app_secret="SECRET-SYNTHETIC-NOT-REAL",
        workbook_id="BOOK-SYNTHETIC-NOT-REAL",
        sheet_id="SHEET-SYNTHETIC-NOT-REAL",
        operator_id="OPERATOR-SYNTHETIC-NOT-REAL",
    )
    transport = RecordingTransport(responses)
    client = DingTalkClient(config, http=transport, sleep=lambda seconds: None)
    """令牌用明显可辨的示意形态，不是平台真实令牌。"""
    client._token = "TOKEN-SYNTHETIC-NOT-REAL"
    client._token_expire_at = 9_999_999_999.0
    answer = client.write_row(row_key, values)
    return answer, transport.requests


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python scripts/recorded_smoke.py")
    parser.add_argument("--source", default="recorded", choices=["recorded", "live"],
                        help="recorded=本地合成回放；live=真实接口")
    parser.add_argument("--batch", default="B2026-0001", help="写回台账时用的行键，默认 B2026-0001")
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        client, transport = (_live_client() if args.source == "live" else _recorded_client())
    except RuntimeError as exc:
        print("冒烟无法开始：%s" % exc, file=sys.stderr)
        return EXIT_CONFIG

    try:
        items = client.fetch_all("products")
    except Exception as exc:
        return _fail("拉取商品失败：%s" % exc)
    if not items:
        return _fail("来源里没有商品记录")

    sku = str(items[0].get("sku", ""))
    # 报关字段的真实来源是报关资料明细；取不到就如实报，不拿产品列表的品名凑数。
    try:
        declarations = _declared_records(client)
    except Exception as exc:
        return _fail("拉取报关资料明细失败：%s" % exc)
    if not declarations:
        return _fail(
            "没有取到报关资料明细（customsDeclaration/batchDetail）：中文报关名/材质/用途/HSCODE 在官方接口里来自该接口，"
            "不在产品列表；请确认账号已开通报关资料权限并存在报关单"
        )
    # 走与线上同一段映射：产品列表 + 报关明细 -> 内部形状。
    mapped, _notes = lingxing_map.map_products(items, declarations)
    if not mapped:
        return _fail("产品列表与报关明细里没有可用的 SKU")
    merged = mapped.get(sku) or mapped[sorted(mapped)[0]]
    fields = product_declare_fields(merged)
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
        described = item.describe() if hasattr(item, "describe") else {}
        print("request %s %s headers=%s body=%s" % (
            described.get("method"), described.get("url"),
            json.dumps(described.get("headers"), ensure_ascii=False), described.get("body_keys")))
    print("write_ok sheetId=%s rowId=%s range=%s" % (
        answer.get("sheetId", ""), answer.get("rowId", ""), answer.get("range", "")))
    print("冒烟通过：取商品报关字段 + 写回台账都走到了 HTTP 调用这一层（来源 %s）" % args.source)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
