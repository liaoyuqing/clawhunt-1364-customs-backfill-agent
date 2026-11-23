"""AC4/AC6：core.cli 是真入口，能被 subprocess 独立跑通，且失败路径退出码非 0。"""

from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "fixtures", "lingxing")
RULES = os.path.join(ROOT, "rules")


def _child_env():
    """子进程 stdout 固定 UTF-8，避免在 cp936 等非 UTF-8 代码页的机器上假失败。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run_cli(*args):
    """用当前解释器跑真入口，不 import core.cli，保证验的是「命令行能不能跑」。"""
    return subprocess.run(
        [sys.executable, "-m", "core.cli"] + list(args),
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_child_env(),
        timeout=180,
    )


def test_cli_prints_json_summary_for_batch():
    proc = run_cli("--batch", "B2026-0001", "--fixtures", FIXTURES, "--rules", RULES)
    assert proc.returncode == 0, proc.stderr
    summary = json.loads(proc.stdout)
    assert summary["batch_no"] == "B2026-0001"
    assert summary["offline"] is True
    assert summary["ok"] is True
    assert summary["module2_fill"]["rows"] == 4
    assert summary["module2_fill"]["columns"] == len(summary["ledger_columns"])
    assert summary["module2_fill"]["columns"] == 23
    assert summary["module2_fill"]["sheet_rows"] == summary["module2_fill"]["rows"]
    assert summary["module2_fill"]["issues"] == 0
    assert summary["module2_fill"]["pending"] == 0
    """73 格 = 模块二 16 列 × 4 行（含表头）+ 模块四 7 列 × 4 行 - 未写的空格。"""
    assert summary["module2_fill"]["sources"] == {"api": 67, "config": 6}
    assert summary["module1_contract"]["contract_no"] == "HT-2026-0001"
    assert summary["module1_contract"]["lines"] == 3
    assert set(summary["module1_contract"]["requirement_gaps"]) <= {
        "missing_requirement", "stamp_missing", "empty_contract", "name_mismatch",
    }
    assert summary["human_gate"]["approved"] is False
    assert "无人工确认" in summary["human_gate"]["reason"]


def test_cli_fills_module4_seven_columns():
    """模块四在同一入口里跑：7 列有值，归档链接是 https，发票按品类扣减。"""
    proc = run_cli("--batch", "B2026-0001")
    assert proc.returncode == 0, proc.stderr
    summary = json.loads(proc.stdout)
    module4 = summary["module4_invoice_ledger"]
    assert module4["rows"] == 3
    assert module4["categories"] == ["礼品", "家居"]
    assert module4["archive_link"].startswith("https://")
    assert len(module4["columns"]) == 7
    assert module4["columns"] == ["开票公司", "发票号", "开票日期", "耗用发票数量", "发票剩余可用数量", "结余", "归档共享链接"]
    usage = {item["category"]: item for item in module4["usage"]}
    assert usage["礼品"]["used"] == "23"
    assert usage["礼品"]["remaining"] == "127.00"
    assert usage["家居"]["remaining"] == "1192.00"
    assert summary["module1_name_book"]["new"] >= 1


def test_cli_module3_reports_invoice_differences():
    proc = run_cli("--batch", "B2026-0001")
    assert proc.returncode == 0, proc.stderr
    invoice = json.loads(proc.stdout)["module3_invoice"]
    assert invoice["invoices"] == 2
    assert invoice["clean"] == 1
    assert invoice["dirty"] == 1
    assert invoice["differences"] == 1
    assert invoice["fields"] == ["税号"]
    assert [item["invoice_no"] for item in invoice["results"]] == ["INV-2026-000101", "INV-2026-000102"]


def test_cli_unknown_batch_exits_nonzero_with_chinese_error():
    proc = run_cli("--batch", "B2099-9999")
    assert proc.returncode != 0
    assert proc.stdout.strip() == ""
    assert "批次不存在" in proc.stderr
    assert "B2099-9999" in proc.stderr


def test_cli_missing_rules_exits_nonzero_with_chinese_error():
    proc = run_cli("--batch", "B2026-0001", "--rules", os.path.join(ROOT, "rules_not_here"))
    assert proc.returncode != 0
    assert proc.stdout.strip() == ""
    assert "规则包加载失败" in proc.stderr


def test_cli_is_offline_and_uses_fixture_transport():
    """入口只读夹具目录：把夹具指到空目录，必须报夹具错误而不是去连网。"""
    proc = run_cli("--batch", "B2026-0001", "--fixtures", os.path.join(ROOT, "fixtures_not_here"))
    assert proc.returncode != 0
    assert "夹具加载失败" in proc.stderr
