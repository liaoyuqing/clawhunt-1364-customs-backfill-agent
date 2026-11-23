"""AC4/AC6：prepublish.check 是真入口，合规配置退 0，三项全错退 2 且逐条列出。"""

from __future__ import annotations

import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE = os.path.join(ROOT, "deploy", "isolated", "isolated.example.yaml")


def _child_env():
    """子进程 stdout 固定 UTF-8，避免在 cp936 等非 UTF-8 代码页的机器上假失败。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run_check(path, module="prepublish.check"):
    return subprocess.run(
        [sys.executable, "-m", module, "--config", str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_child_env(),
        timeout=120,
    )


def write_yaml(tmp_path, payload, name="isolated.yaml"):
    path = tmp_path / name
    with open(path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, allow_unicode=True, sort_keys=False)
    return path


def test_prepublish_example_config_exits_zero():
    proc = run_check(EXAMPLE)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().splitlines()[-1] == "ok"
    assert "problems" not in proc.stdout
    assert proc.stderr.strip() == ""


def test_prepublish_python_dash_m_also_works():
    proc = run_check(EXAMPLE, module="prepublish")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().splitlines()[-1] == "ok"


def test_prepublish_bad_config_lists_three_problems_and_exits_two(tmp_path):
    bad = {
        "egress_whitelist": ["10.20.0.0/16", "8.8.8.8/32"],
        "model_base_url": "https://api.example.com/v1",
        "archive_path": os.path.join(os.sep, "Users", "bob", "OneDrive", "invoice-archive"),
    }
    proc = run_check(write_yaml(tmp_path, bad))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    assert "problems: 3" in lines
    problems = [line for line in lines if line.startswith("[")]
    assert len(problems) == 3
    assert "public network in egress whitelist" in proc.stdout
    assert "model base_url is not a local address" in proc.stdout
    assert "cloud sync folder" in proc.stdout


def test_prepublish_172_public_segment_is_rejected(tmp_path):
    """172.16/12 才是私有段：172.32 以上必须按公网拒绝，规则与 core/preflight.py 一致。"""
    cfg = {
        "egress_whitelist": ["172.20.0.0/16"],
        "model_base_url": "http://172.40.0.5:8000/v1",
        "archive_path": os.path.join(os.sep, "srv", "invoice-archive"),
    }
    proc = run_check(write_yaml(tmp_path, cfg))
    assert proc.returncode == 2
    assert "problems: 1" in proc.stdout
    assert "model base_url is not a local address" in proc.stdout


def test_prepublish_missing_config_file_exits_nonzero():
    proc = run_check(os.path.join(ROOT, "deploy", "isolated", "not_here.yaml"))
    assert proc.returncode != 0
    assert proc.returncode != 2
    assert "配置加载失败" in proc.stderr


def test_prepublish_reuses_core_preflight_functions():
    """判定逻辑只有一份：prepublish.check.run 的 problems 与 core.preflight.run_checks 逐条相同。"""
    from core.preflight import run_checks
    from prepublish.check import run

    result = run(EXAMPLE)
    expected = run_checks(yaml.safe_load(open(EXAMPLE, encoding="utf-8")))
    assert result["problems"] == expected["problems"]
    assert result["ok"] == expected["ok"] is True