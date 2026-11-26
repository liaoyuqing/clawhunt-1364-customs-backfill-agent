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
def test_endpoint_name_separates_workbook_create_from_sheet_listing():
    """POST /v1.0/doc/workbooks 是「建工作簿」（接口不存在，实测 404），与 GET .../sheets 不是一件事。

    两者都被判成 dingtalk_sheets 会让 manifest 读起来像「列工作表失败」，掩盖真实结论。
    """
    assert live_capture._endpoint_name("https://api.dingtalk.com/v1.0/doc/workbooks", "POST") == (
        "dingtalk_workbook_create"
    )
    assert live_capture._endpoint_name("https://api.dingtalk.com/v1.0/doc/workbooks/B/sheets", "GET") == (
        "dingtalk_sheets"
    )
    assert live_capture._endpoint_name("https://api.dingtalk.com/v1.0/doc/workspaces/W/docs", "POST") == (
        "dingtalk_workspace_docs"
    )
    assert live_capture._endpoint_name("https://api.dingtalk.com/v2.0/wiki/workspaces", "GET") == (
        "dingtalk_wiki_workspaces"
    )


def test_denials_do_not_make_a_probe_capture_incomplete(tmp_path, monkeypatch):
    """探测型抓取：平台拒绝是答案不是故障，必须进 denials[] 且 complete 仍为 true。

    反过来，非探测模式下同样的 4xx 必须进 failures[] 并把 complete 拉成 false——
    这条断言守住「探测结论」和「抓取失败」不被混成一件事。
    """
    from core.http_client import HttpRequest

    request = HttpRequest(
        method="POST",
        url="https://api.dingtalk.com/v1.0/doc/workbooks",
        headers={"x-acs-dingtalk-access-token": "TOKEN-SYNTHETIC-NOT-REAL"},
    )
    body = json.dumps({"code": "InvalidAction.NotFound", "message": "Specified api is not found"})

    probing = live_capture.CaptureRun(str(tmp_path / 'a'), 'dingtalk', 'https://api.dingtalk.com', [], replayable=False)
    os.makedirs(str(tmp_path / 'a'), exist_ok=True)
    transport = live_capture.CaptureTransport(probing)
    transport.denial_mode = True
    transport._record_rejection(request, "2026-11-27T01:00:00+08:00", "2026-11-26T17:00:00Z", 404, body, "http 404")
    manifest = probing.manifest()
    assert manifest["failure_count"] == 0
    assert manifest["denial_count"] == 1
    assert manifest["complete"] is True
    assert manifest["denials"][0]["endpoint"] == "/v1.0/doc/workbooks"
    assert manifest["denials"][0]["response_file"].endswith(".denial.json")

    strict = live_capture.CaptureRun(str(tmp_path / 'b'), 'dingtalk', 'https://api.dingtalk.com', [], replayable=False)
    os.makedirs(str(tmp_path / 'b'), exist_ok=True)
    strict_transport = live_capture.CaptureTransport(strict)
    strict_transport._record_rejection(request, "2026-11-27T01:00:00+08:00", "2026-11-26T17:00:00Z", 404, body, "http 404")
    strict_manifest = strict.manifest()
    assert strict_manifest["failure_count"] == 1
    assert strict_manifest["denial_count"] == 0
    assert strict_manifest["complete"] is False


def test_probe_capture_never_claims_customer_evidence(tmp_path):
    """自建表探测必须自带 environment 标注，且不能把 complete 当成「写过客户台账」。"""
    run = live_capture.CaptureRun(str(tmp_path), 'dingtalk', 'https://api.dingtalk.com', [], replayable=False)
    manifest = run.manifest(derived={'probe_create': True, 'environment': 'self_built_test_table'})
    assert manifest["derived"]["environment"] == "self_built_test_table"
    assert manifest["origin"] == "live"
    assert "denials" in manifest

def test_write_sample_requires_readback_match():
    """写入必须跟着一次回读：接口返回成功不等于表里真有这几个字。"""

    class _Client:
        def __init__(self, echo):
            self.echo = echo
            self.calls = []

        def write_range(self, address, values, workbook_id='', sheet_id=''):
            self.calls.append(('write', address))
            return {'range': address}

        def read_range(self, address, workbook_id='', sheet_id=''):
            self.calls.append(('read', address))
            return self.echo

    sample = [['capture-probe', 'PROBE']]
    ok_client = _Client([['capture-probe', 'PROBE']])
    answer = live_capture._write_and_read_back(ok_client, 'B', 'S', sample)
    assert answer["match"] is True
    assert [call[0] for call in ok_client.calls] == ["write", "read"]
    assert answer["range"] == "A1:B1"

    broken = _Client([['', '']])
    try:
        live_capture._write_and_read_back(broken, 'B', 'S', sample)
    except RuntimeError as exc:
        assert "回读不一致" in str(exc)
    else:
        raise AssertionError("读回不一致时必须报错，不能静默返回成功")


def test_probe_mode_keeps_server_errors_as_failures(tmp_path):
    """探测模式只把「平台明确拒绝（4xx）」当答案，5xx 仍算抓取失败。

    背景：知识库权限开通后，`POST /v1.0/doc/workspaces/{假id}/docs` 稳定返回 500 internalError。
    把它记成 denial 会让 manifest 看起来「探测全部完成」，掩盖了一个真实的平台内部错误；
    记成 failure（complete=false）才是如实。这条断言守住这个边界。
    """
    from core.http_client import HttpRequest

    request = HttpRequest(
        method="POST",
        url="https://api.dingtalk.com/v1.0/doc/workspaces/PROBE-WORKSPACE-ID/docs",
        headers={"x-acs-dingtalk-access-token": "TOKEN-SYNTHETIC-NOT-REAL"},
    )
    body = json.dumps({"code": "internalError", "message": "系统内部错误"})

    run = live_capture.CaptureRun(str(tmp_path), 'dingtalk', 'https://api.dingtalk.com', [], replayable=False)
    transport = live_capture.CaptureTransport(run)
    transport.denial_mode = True
    transport._record_rejection(request, "2026-11-27T02:00:00+08:00", "2026-11-26T18:00:00Z", 500, body, "http 500")
    manifest = run.manifest()
    assert manifest["denial_count"] == 0
    assert manifest["failure_count"] == 1
    assert manifest["complete"] is False
    assert manifest["failures"][0]["status"] == 500
    assert manifest["failures"][0]["business_code"] == "internalError"