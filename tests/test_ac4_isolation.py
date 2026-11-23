"""AC4：模块四只在内网跑，三项自检任一不过就拒绝启动；原件不出内网。"""

from __future__ import annotations

import os

import pytest

from core.preflight import (
    assert_ready,
    check_archive_path,
    check_egress_whitelist,
    check_model_base_url,
    run_checks,
)

GOOD = {
    "egress_whitelist": ["10.20.0.0/16", "192.168.10.0/24", "127.0.0.1/32"],
    "model_base_url": "http://127.0.0.1:11434/v1",
    "archive_path": os.path.join(os.sep, "srv", "invoice-archive"),
}


def test_ac4_good_config_passes():
    result = run_checks(GOOD)
    assert result["ok"] is True
    assert result["problems"] == []
    assert_ready(GOOD)


def test_ac4_public_cidr_in_whitelist_fails_and_exits_nonzero():
    bad = dict(GOOD)
    bad["egress_whitelist"] = list(GOOD["egress_whitelist"]) + ["8.8.8.8/32"]
    problems = check_egress_whitelist(bad["egress_whitelist"])
    assert problems == ["public network in egress whitelist: 8.8.8.8/32"]
    with pytest.raises(SystemExit) as exc:
        assert_ready(bad)
    assert exc.value.code == 2


def test_ac4_empty_whitelist_fails():
    assert check_egress_whitelist([]) == ["egress whitelist is empty"]


def test_ac4_model_url_must_be_local():
    assert check_model_base_url("http://127.0.0.1:11434/v1") == []
    assert check_model_base_url("http://192.168.1.10:8000/v1") == []
    assert check_model_base_url("http://172.20.0.5:8000/v1") == []
    assert check_model_base_url("") == ["model base_url is empty"]
    problems = check_model_base_url("https://api.openai.com/v1")
    assert problems == ["model base_url is not a local address: https://api.openai.com/v1"]
    assert check_model_base_url("http://172.40.0.5:8000/v1") != []


def test_ac4_archive_path_must_not_be_cloud_synced():
    assert check_archive_path(os.path.join(os.sep, "srv", "invoice-archive")) == []
    problems = check_archive_path(os.path.join(os.sep, "Users", "bob", "OneDrive", "invoice"))
    assert problems and "cloud sync folder" in problems[0]
    assert check_archive_path(os.path.join(os.sep, "data", "坚果云", "inv")) != []


def test_ac4_all_three_problems_listed_together():
    bad = {
        "egress_whitelist": ["1.1.1.1/32"],
        "model_base_url": "https://api.example.com/v1",
        "archive_path": os.path.join(os.sep, "Users", "x", "Dropbox", "a"),
    }
    result = run_checks(bad)
    assert result["ok"] is False
    assert len(result["problems"]) == 3