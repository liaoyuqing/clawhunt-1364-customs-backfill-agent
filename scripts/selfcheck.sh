#!/usr/bin/env bash
# 干净副本自检：装依赖 -> 生成源数据 -> 参考实现 -> 测试 -> 抽检 -> 密钥扫描，任一步非 0 立即退出。
set -euo pipefail
cd "$(dirname "$0")/.."

PY=${PY:-./.venv/Scripts/python.exe}
if [ ! -x "$PY" ]; then
  PY=python
fi

echo "[1/6] install"
"$PY" -m pip install -q -r requirements.txt

echo "[2/6] fixtures"
"$PY" scripts/make_fixtures.py

echo "[3/6] reference impl"
"$PY" scripts/reference_impl.py

echo "[4/6] pytest"
"$PY" -m pytest -o addopts= -q

echo "[5/6] sample50"
"$PY" scripts/sample50.py

echo "[6/6] secret scan"
"$PY" scripts/secret_scan.py

echo "SELFCHECK OK"