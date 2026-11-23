"""模块四启动自检命令：python -m prepublish.check --config <yaml>。

三项检查全部复用 core/preflight.py 里的判定函数，这里只负责读配置、打印、定退出码：
  - 出网白名单每一条都是私有网段；
  - model_base_url 指向回环或内网（172.16/12 私有段规则与 preflight 一致）；
  - 原件目录不在 OneDrive / Dropbox / Google Drive / iCloud / 坚果云 / 百度网盘 等云同步路径下。
全过打印 ok 并退出 0；任一不过逐条打印 problems 并退出 2；配置读不到或不是合法 YAML 退出 1。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Sequence

import yaml

from core.preflight import CLOUD_PREFIXES, run_checks

EXIT_OK = 0
EXIT_CONFIG_BAD = 1
EXIT_NOT_READY = 2

REQUIRED_KEYS = ("egress_whitelist", "model_base_url", "archive_path")


class ConfigFileError(RuntimeError):
    """配置文件读不到、不是合法 YAML，或缺少必需键。"""


def load_config(path: str) -> Dict[str, Any]:
    if not path:
        raise ConfigFileError("必须用 --config 指定自检配置路径")
    if not os.path.exists(path):
        raise ConfigFileError("配置文件不存在：%s" % path)
    try:
        with open(path, encoding="utf-8") as handle:
            doc = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ConfigFileError("配置文件不是合法 YAML：%s（%s）" % (path, exc))
    if doc is None:
        raise ConfigFileError("配置文件是空的：%s" % path)
    if not isinstance(doc, dict):
        raise ConfigFileError("配置文件顶层必须是映射：%s" % path)
    missing = [key for key in REQUIRED_KEYS if key not in doc]
    if missing:
        raise ConfigFileError("配置文件缺少必需键：%s" % "、".join(missing))
    if not isinstance(doc.get("egress_whitelist"), list):
        raise ConfigFileError("egress_whitelist 必须是列表")
    return doc


def run(config_path: str) -> Dict[str, Any]:
    """返回 {ok, problems, config}；判定逻辑一行都不复制，全部来自 core.preflight。"""
    config = load_config(config_path)
    result = run_checks(config)
    return {"ok": bool(result["ok"]), "problems": list(result["problems"]), "config": config}


def _print_report(result: Dict[str, Any], config_path: str) -> None:
    config = result["config"]
    print("config: %s" % config_path)
    print("egress_whitelist: %s" % json.dumps(config.get("egress_whitelist", []), ensure_ascii=False))
    print("model_base_url: %s" % config.get("model_base_url", ""))
    print("archive_path: %s" % config.get("archive_path", ""))
    print("cloud_sync_markers: %s" % "、".join(CLOUD_PREFIXES))
    if result["ok"]:
        print("ok")
        return
    print("problems: %d" % len(result["problems"]))
    for index, problem in enumerate(result["problems"], start=1):
        print("  [%d] %s" % (index, problem))


def _force_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass


def main(argv: Optional[Sequence[str]] = None) -> int:
    _force_utf8_stdout()
    parser = argparse.ArgumentParser(
        prog="python -m prepublish.check",
        description="模块四启动自检：出网白名单 / 本地模型地址 / 原件目录三项，任一不过退出码 2",
    )
    parser.add_argument("--config", required=True, help="自检配置 yaml，例如 deploy/isolated/isolated.example.yaml")
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        result = run(args.config)
    except ConfigFileError as exc:
        print("配置加载失败：%s" % exc, file=sys.stderr)
        return EXIT_CONFIG_BAD
    _print_report(result, args.config)
    return EXIT_OK if result["ok"] else EXIT_NOT_READY


def problems_of(result: Dict[str, Any]) -> List[str]:
    """给测试与调用方用的便利函数。"""
    return list(result["problems"])


if __name__ == "__main__":
    sys.exit(main())
