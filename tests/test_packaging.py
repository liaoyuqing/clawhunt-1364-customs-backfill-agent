"""包结构与交付物自检：星号导入不报错、模块四镜像与自检脚本步骤齐全。"""

from __future__ import annotations

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_star_import_from_core_works():
    """`from core import *` 必须不报错（旧版 __all__ 里写了不存在的 invoice_check）。"""
    import core

    namespace = {}
    exec("from core import *", namespace)
    for name in core.__all__:
        assert name in namespace, name
    for name in ("config", "ledger", "declare", "module4", "pipeline", "invoice", "preflight", "cli"):
        assert name in core.__all__
    assert core.__version__


def test_core_all_names_are_real_modules():
    import importlib

    import core

    missing = []
    for name in core.__all__:
        try:
            importlib.import_module("core." + name)
        except ImportError as exc:
            missing.append("%s (%s)" % (name, exc))
    assert missing == []


def test_module4_dockerfile_exists_and_uses_slim_base():
    path = os.path.join(ROOT, "deploy", "isolated", "Dockerfile")
    assert os.path.exists(path)
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    base = [line for line in text.splitlines() if line.startswith("FROM ")][0]
    assert base == "FROM python:3.11-slim"
    assert "pip install --no-cache-dir -r /app/requirements.txt" in text
    assert "prepublish.check" in text
    assert "HEALTHCHECK" in text


def test_compose_builds_local_image_and_has_healthcheck():
    path = os.path.join(ROOT, "deploy", "isolated", "docker-compose.yml")
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    assert "build:" in text
    assert "dockerfile: deploy/isolated/Dockerfile" in text
    assert "healthcheck:" in text
    assert "internal: true" in text
    assert "image: clawhunt-1364-module4:latest" in text


def test_selfcheck_steps_are_numbered_one_to_six():
    path = os.path.join(ROOT, "scripts", "selfcheck.sh")
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    steps = re.findall(r"\[(\d+)/(\d+)\]", text)
    assert [int(index) for index, _total in steps] == [1, 2, 3, 4, 5, 6]
    assert {total for _index, total in steps} == {"6"}


def test_requirements_has_no_new_dependencies():
    path = os.path.join(ROOT, "requirements.txt")
    with open(path, encoding="utf-8") as handle:
        lines = [line.strip() for line in handle if line.strip() and not line.startswith("#")]
    names = sorted(line.split("==")[0].lower() for line in lines)
    assert names == ["pytest", "pyyaml"]


def test_gitignore_excludes_runtime_artifacts():
    path = os.path.join(ROOT, ".gitignore")
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    for entry in (".venv/", "__pycache__/", ".pytest_cache/", "logs/"):
        assert entry in text