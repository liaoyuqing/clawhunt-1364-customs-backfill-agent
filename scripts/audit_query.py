"""按批次号检索审计日志：python scripts/audit_query.py --batch B2026-0001

审计行是 JSONL，每行含操作人、动作、时间、载荷摘要与批次号；
日志默认落在仓库 logs/audit.log，可用 --log 指定其它路径。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.gates import DEFAULT_AUDIT_PATH, AuditLog  # noqa: E402

EXIT_OK = 0
EXIT_USAGE = 2


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python scripts/audit_query.py",
        description="按批次号检索审计日志（JSONL）",
    )
    parser.add_argument("--batch", default=None, help="批次号，例如 B2026-0001")
    parser.add_argument("--action", default=None, help="动作名，例如 tax_bureau_submit")
    parser.add_argument("--operator", default=None, help="操作人")
    parser.add_argument("--log", default=None, help="审计日志路径，默认 logs/audit.log")
    parser.add_argument("--json", action="store_true", help="以 JSON 数组输出")
    args = parser.parse_args(list(argv) if argv is not None else None)

    path = args.log or DEFAULT_AUDIT_PATH
    log = AuditLog(path=path)
    rows = log.query(batch_no=args.batch, action=args.action, operator=args.operator)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return EXIT_OK
    if not rows:
        if not os.path.exists(path):
            print("审计日志不存在：%s（还没有产生过审计记录）" % path)
        else:
            print("没有匹配的审计记录（日志 %s）" % path)
        return EXIT_OK
    print("审计日志 %s 共 %d 条" % (path, len(rows)))
    for row in rows:
        print(
            "%s | 操作人 %s | 动作 %s | 摘要 %s | 时间 %s"
            % (
                row.get("batch_no", ""),
                row.get("operator", ""),
                row.get("action", ""),
                row.get("digest", ""),
                row.get("at", ""),
            )
        )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
