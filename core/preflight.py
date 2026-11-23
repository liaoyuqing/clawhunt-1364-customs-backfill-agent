"""模块四启动自检：三项不满足就拒绝启动（退出码非 0）。"""

from __future__ import annotations

import ipaddress
import os
from typing import Any, Dict, List

CLOUD_PREFIXES = ("OneDrive", "Dropbox", "Google Drive", "iCloud", "坚果云", "百度网盘")
LOOPBACK_HOSTS = ("127.0.0.1", "localhost")
PRIVATE_PREFIXES = ("10.", "192.168.")
RFC1918_B_LO = 16
RFC1918_B_HI = 31


def check_egress_whitelist(cidrs: List[str]) -> List[str]:
    """白名单每一条都必须是私有网段，出现公网地址即失败。"""
    problems: List[str] = []
    if not cidrs:
        problems.append("egress whitelist is empty")
    for entry in cidrs:
        try:
            net = ipaddress.ip_network(str(entry), strict=False)
        except ValueError:
            problems.append("unparsable cidr: %s" % entry)
            continue
        if not net.is_private:
            problems.append("public network in egress whitelist: %s" % entry)
    return problems


def check_model_base_url(url: str) -> List[str]:
    """本地模型地址：必须指向回环或内网。"""
    text = str(url or "").strip()
    if not text:
        return ["model base_url is empty"]
    host = text.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
    if host in LOOPBACK_HOSTS or host.startswith(PRIVATE_PREFIXES):
        return []
    if host.startswith("172."):
        """172.16/12 才是私有段，172.32 以上属于公网，必须拒绝。"""
        parts = host.split(".")
        try:
            second = int(parts[1])
        except (IndexError, ValueError):
            return ["cannot parse model host: %s" % host]
        if RFC1918_B_LO <= second <= RFC1918_B_HI:
            return []
    return ["model base_url is not a local address: %s" % text]


def check_archive_path(path: str) -> List[str]:
    """原件目录不得落在任何云同步路径下。"""
    text = os.path.abspath(str(path or ""))
    if not text:
        return ["archive path is empty"]
    for marker in CLOUD_PREFIXES:
        if marker.lower() in text.lower():
            return ["archive path is inside a cloud sync folder (%s): %s" % (marker, text)]
    return []


def run_checks(config: Dict[str, Any]) -> Dict[str, Any]:
    problems: List[str] = []
    problems.extend(check_egress_whitelist(list(config.get("egress_whitelist", []))))
    problems.extend(check_model_base_url(str(config.get("model_base_url", ""))))
    problems.extend(check_archive_path(str(config.get("archive_path", ""))))
    return {"ok": not problems, "problems": problems}


def assert_ready(config: Dict[str, Any]) -> None:
    result = run_checks(config)
    if not result["ok"]:
        raise SystemExit(2)