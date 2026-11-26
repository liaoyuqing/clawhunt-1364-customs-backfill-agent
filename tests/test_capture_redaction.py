"""抓取器落盘前的脱敏：证据目录里不许出现 operatorId（个人身份）与令牌。

背景：平台的报错原文会把整条 URL（含 query）回显出来，例如
  _transport_error: "http 400 from ...?operatorId=Dyabcdef0123456789"
只对 query 数组打码是不够的——原文里那一份会漏出去。
"""

from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import live_capture  # noqa: E402

OPERATOR = "Dy0123456789abcdef0123456789ab"


def _run(tmp_path):
    run = live_capture.CaptureRun(str(tmp_path), "dingtalk", "https://api.dingtalk.com", [], replayable=False)
    return run


def test_operator_id_inside_error_text_is_redacted(tmp_path):
    """错误原文里回显的 operatorId 必须打码：它等同于操作人身份。"""
    run = _run(tmp_path)
    document = {
        "_transport_error": "http 400 from https://api.dingtalk.com/v1.0/doc/workbooks/B/sheets?operatorId=%s" % OPERATOR,
        "status": 400,
    }
    name = run._write("02-dingtalk_sheets.failure.json", document)
    text = (tmp_path / name).read_text(encoding="utf-8")
    assert OPERATOR not in text
    assert "operatorId=***" in text


def test_other_query_values_are_left_alone(tmp_path):
    """只打码凭据类参数，offset / length 这类业务参数保持原文，证据才可核对。"""
    run = _run(tmp_path)
    document = {"_transport_error": "http 400 from https://x.invalid/p?offset=100&length=1000&app_key=SECRETVALUE1"}
    name = run._write("t.json", document)
    text = (tmp_path / name).read_text(encoding="utf-8")
    assert "offset=100" in text and "length=1000" in text
    assert "SECRETVALUE1" not in text


def test_registered_secret_is_redacted_anywhere_in_manifest(tmp_path):
    """登记过的令牌/密钥出现在任何字段里都要打码，包括平台原文。"""
    run = _run(tmp_path)
    run.add_secret("APPKEY0123456789abcdef")
    document = {"note": "server echoed APPKEY0123456789abcdef back"}
    name = run._write("t.json", document)
    text = (tmp_path / name).read_text(encoding="utf-8")
    assert "APPKEY0123456789abcdef" not in text
