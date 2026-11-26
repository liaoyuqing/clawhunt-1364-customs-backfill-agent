"""买家退返第 7 条：抽检必须标明数据来源，合成自比不能冒充真实源口径。

同时验证 source_data 取数层：三个来源给出同一套数据形状，来源信息随数据返回。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from source_data import SourceDataError, load_source_data  # noqa: E402


def _child_env():
    """子进程 stdout 固定 UTF-8，避免在 cp936 等非 UTF-8 代码页的机器上假失败。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run_sample50(*args):
    return subprocess.run(
        [sys.executable, os.path.join("scripts", "sample50.py")] + list(args),
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_child_env(),
        timeout=300,
    )


def test_sample50_defaults_to_recorded_source():
    """默认来源是 recorded（本地合成录制回放），报告里必须写明来源与取数时间。"""
    proc = _run_sample50()
    assert proc.returncode == 0, proc.stderr
    with open(os.path.join(ROOT, "reports", "sample50_report.json"), encoding="utf-8") as handle:
        report = json.load(handle)
    assert report["source"] == "recorded"
    assert report["source_synthetic"] is True
    assert report["source_fetched_at"]
    assert "source=recorded(synthetic)" in report["source_label"]
    assert report["source_files"]


def test_sample50_report_keeps_existing_keys():
    """既有键一个都不能少，抽检口径没有被改写。"""
    proc = _run_sample50("--source", "recorded")
    assert proc.returncode == 0, proc.stderr
    with open(os.path.join(ROOT, "reports", "sample50_report.json"), encoding="utf-8") as handle:
        report = json.load(handle)
    for key in (
        "batches", "rows", "columns", "cells_compared", "cells_consistent", "cells_undecidable",
        "consistency_rate", "passes_threshold", "source_kind_missing", "bad_source_kinds",
        "differences", "difference_count", "issues", "issue_count",
    ):
        assert key in report, key
    assert report["columns"] == 23


def test_source_errors_are_listed_and_excluded_from_denominator():
    """源系统自身的错误单独列出，不计入一致率分母。"""
    proc = _run_sample50("--source", "recorded")
    assert proc.returncode == 0, proc.stderr
    with open(os.path.join(ROOT, "reports", "sample50_report.json"), encoding="utf-8") as handle:
        report = json.load(handle)
    assert report["source_error_count"] == len(report["source_errors"])
    assert report["source_error_count"] >= 1
    assert report["cells_undecidable"] >= report["source_error_count"]
    total = report["cells_consistent"] + report["cells_undecidable"] + report["difference_count"]
    assert total == report["cells_compared"] + report["cells_undecidable"]


def test_fixture_source_is_explicitly_marked_synthetic():
    """合成夹具自比路径保留，但必须显式标注 source=fixture(synthetic)。"""
    proc = _run_sample50("--source", "fixture")
    assert proc.returncode == 0, proc.stderr
    with open(os.path.join(ROOT, "reports", "sample50_report.json"), encoding="utf-8") as handle:
        report = json.load(handle)
    assert report["source"] == "fixture"
    assert report["source_synthetic"] is True
    assert report["source_label"].startswith("source=fixture(synthetic)")


def test_lingxing_source_requires_environment_variables():
    """真实源缺环境变量必须报错退出，不得静默回退到合成数据。

    官方 v2 用 appId（不是上一轮的 app_key），所以这里清的就是 LINGXING_APP_ID。
    """
    env = _child_env()
    for key in ("LINGXING_BASE_URL", "LINGXING_APP_ID", "LINGXING_APP_KEY", "LINGXING_APP_SECRET"):
        env.pop(key, None)
    proc = subprocess.run(
        [sys.executable, os.path.join("scripts", "sample50.py"), "--source", "lingxing"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=120,
    )
    assert proc.returncode == 2
    assert "LINGXING_BASE_URL" in proc.stderr


def test_source_data_layer_returns_same_shape_for_every_source():
    fixture = load_source_data("fixture")
    recorded = load_source_data("recorded")
    assert len(fixture["batches"]) == len(recorded["batches"]) == 50
    assert len(fixture["products"]) == len(recorded["products"]) == 11
    assert len(fixture["suppliers"]) == len(recorded["suppliers"]) == 3
    assert fixture["provenance"]["source"] == "fixture"
    assert recorded["provenance"]["source"] == "recorded"
    assert recorded["provenance"]["synthetic"] is True


def test_unknown_source_is_rejected():
    try:
        load_source_data("nope")
    except SourceDataError as exc:
        assert "未知数据来源" in str(exc)
    else:
        raise AssertionError("未知来源必须报错")

def test_recorded_source_is_never_counted_as_ac1_evidence():
    """买方第 2 次驳回的正题：合成源自比不能当成 AC#1 的达标证据。

    报告里必须同时出现三件事：origin=recorded、counts_as_ac1_evidence=false、
    ac1_evidence_status=unverified_on_live_account，并且给出理由与客户侧复现步骤。
    """
    proc = _run_sample50("--source", "recorded")
    assert proc.returncode == 0, proc.stderr
    with open(os.path.join(ROOT, "reports", "sample50_report.json"), encoding="utf-8") as handle:
        report = json.load(handle)
    assert report["source_origin"] == "recorded"
    assert report["counts_as_ac1_evidence"] is False
    assert report["ac1_evidence_status"] == "unverified_on_live_account"
    assert "未在真实账号上取得" in report["reason"]
    assert report["reproduce_steps"] and any("live_capture.py" in step for step in report["reproduce_steps"])
    # 一致率照样算出来（引擎自洽性），但不会被说成验收结论
    assert report["consistency_rate"] >= 0.995


def test_fixture_source_origin_is_fixture_not_live():
    """夹具源的 origin 必须是 fixture，不能让任何非 live 口径混进 live。"""
    proc = _run_sample50("--source", "fixture")
    assert proc.returncode == 0, proc.stderr
    with open(os.path.join(ROOT, "reports", "sample50_report.json"), encoding="utf-8") as handle:
        report = json.load(handle)
    assert report["source_origin"] == "fixture"
    assert report["counts_as_ac1_evidence"] is False


def test_capture_source_rejects_a_directory_whose_manifest_is_not_live():
    """抓取目录的 manifest 若不是 origin=live，回放入口必须拒绝（合成数据不许冒充）。"""
    import tempfile

    from core.sources import SourceError, capture_payloads

    with tempfile.TemporaryDirectory() as folder:
        manifest = {
            "origin": "recorded",
            "endpoints": [{"name": "products", "response_file": "01-products.response.json"}],
        }
        with open(os.path.join(folder, "manifest.json"), "w", encoding="utf-8") as handle:
            json.dump(manifest, handle)
        with open(os.path.join(folder, "01-products.response.json"), "w", encoding="utf-8") as handle:
            json.dump({"code": 0, "data": []}, handle)
        try:
            capture_payloads(folder)
        except SourceError as exc:
            assert "不是 live" in str(exc)
        else:
            raise AssertionError("非 live 的抓取目录必须被拒绝")


def test_capture_source_replays_a_live_manifest():
    """origin=live 的抓取目录能回放，并且 provenance 如实写 live + 主机 + 取数时间。"""
    import tempfile

    from core.sources import load_capture

    products = {
        "code": 0,
        "message": "success",
        "total": 1,
        "data": [{"id": 1, "sku": "SKU-1001", "product_name": "产品甲", "cg_price": "10.00", "category_name": "家居"}],
    }
    suppliers = {
        "code": 0,
        "message": "success",
        "total": 1,
        "data": [{"supplier_id": "S1", "supplier_name": "供应商甲", "contact_person": "张三", "credit_code": "91440300MA5F0001XA"}],
    }
    with tempfile.TemporaryDirectory() as folder:
        for name, payload in (("products", products), ("suppliers", suppliers)):
            with open(os.path.join(folder, "01-%s.response.json" % name), "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
        manifest = {
            "origin": "live",
            "host": "https://openapi.lingxing.com",
            "created_at": "2026-11-27T00:00:00+08:00",
            "endpoints": [
                {"name": "products", "response_file": "01-products.response.json"},
                {"name": "suppliers", "response_file": "01-suppliers.response.json"},
            ],
        }
        with open(os.path.join(folder, "manifest.json"), "w", encoding="utf-8") as handle:
            json.dump(manifest, handle)
        batches, mapped_products, mapped_suppliers, provenance = load_capture(folder)
    assert provenance["origin"] == "live"
    assert provenance["host"] == "https://openapi.lingxing.com"
    assert provenance["fetched_at"] == "2026-11-27T00:00:00+08:00"
    assert "SKU-1001" in mapped_products
    assert mapped_products["SKU-1001"]["declare_name"] == "产品甲"
    assert mapped_suppliers["S1"]["tax_no"] == "91440300MA5F0001XA"
    # 只有产品/供应商、没有货件：批次为空，这是如实结果而不是报错
    assert batches == []

def test_capture_source_rejects_a_dingtalk_capture_directory():
    """钉钉侧的抓取目录（manifest.source=dingtalk）不能拿来算 AC#1 一致率。"""
    import tempfile

    from core.sources import SourceError, capture_payloads

    with tempfile.TemporaryDirectory() as folder:
        manifest = {
            "origin": "live",
            "source": "dingtalk",
            "complete": True,
            "replayable": False,
            "endpoints": [{"name": "dingtalk_token", "response_file": "01.token.json"}],
        }
        with open(os.path.join(folder, "manifest.json"), "w", encoding="utf-8") as handle:
            json.dump(manifest, handle)
        with open(os.path.join(folder, "01.token.json"), "w", encoding="utf-8") as handle:
            json.dump({"_keys": ["accessToken"]}, handle)
        try:
            capture_payloads(folder)
        except SourceError as exc:
            assert "不是领星业务接口" in str(exc)
        else:
            raise AssertionError("钉钉抓取目录必须被拒绝")


def test_capture_source_rejects_an_incomplete_capture():
    """complete=false 的抓取目录（有端点失败）不能当作一致率证据源。"""
    import tempfile

    from core.sources import SourceError, capture_payloads

    with tempfile.TemporaryDirectory() as folder:
        manifest = {
            "origin": "live",
            "source": "lingxing",
            "complete": False,
            "replayable": True,
            "failure_count": 2,
            "endpoints": [{"name": "products", "response_file": "01.json"}],
        }
        with open(os.path.join(folder, "manifest.json"), "w", encoding="utf-8") as handle:
            json.dump(manifest, handle)
        with open(os.path.join(folder, "01.json"), "w", encoding="utf-8") as handle:
            json.dump({"code": 0, "data": []}, handle)
        try:
            capture_payloads(folder)
        except SourceError as exc:
            assert "不完整" in str(exc)
        else:
            raise AssertionError("不完整的抓取必须被拒绝")
