#!/usr/bin/env bash
# 干净副本自检：装依赖 -> 跑测试 -> 生成源数据 -> 抽检 -> 密钥扫描，任一步非 0 立即退出。
set -euo pipefail
cd "$(dirname "$0")/.."

PY=${PY:-./.venv/Scripts/python.exe}
if [ ! -x "$PY" ]; then
  PY=python
fi

echo "[1/5] install"
"$PY" -m pip install -q -r requirements.txt

echo "[2/5] fixtures"
"$PY" scripts/make_fixtures.py

echo "[3/5] reference impl"
"$PY" scripts/reference_impl.py

echo "[4/5] pytest"
"$PY" -m pytest -o addopts= -q

echo "[5/5] sample50"
"$PY" scripts/sample50.py
echo "[6/6] secret scan"
"$PY" scripts/secret_scan.py

echo "SELFCHECK OK"