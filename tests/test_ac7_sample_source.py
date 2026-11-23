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
    """真实源缺环境变量必须报错退出，不得静默回退到合成数据。"""
    env = _child_env()
    for key in ("LINGXING_BASE_URL", "LINGXING_APP_KEY", "LINGXING_APP_SECRET"):
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
