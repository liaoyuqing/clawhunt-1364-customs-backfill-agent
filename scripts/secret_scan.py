"""密钥扫描：六类模式，扫工作区文件与 git 全历史，命中即非 0 退出。

历史扫描必须在 Windows 上也真的跑起来：git 输出固定按 UTF-8 解码（errors="replace"），
不再依赖 text=True 的平台默认编码（GBK 会在 reader 线程抛 UnicodeDecodeError，
p.stdout 变成 None，历史扫描被静默跳过 —— 那等于假绿）。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PATTERNS = [
    ("clawhunt_agent_key", "cp" + "h_[A-Za-z0-9]{16,}"),
    ("github_oauth", "gh" + "o_[A-Za-z0-9]{20,}"),
    ("github_pat", "gh" + "p_[A-Za-z0-9]{20,}"),
    ("github_fine_pat", "github_" + "pat_[A-Za-z0-9_]{20,}"),
    ("openai_sk", r"(?<![A-Za-z0-9])s" + "k-[A-Za-z0-9]{20,}"),
    ("aws_akid", "AK" + "IA[0-9A-Z]{16}"),
]

SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", "logs"}
SKIP_FILES = {"metrics.json", "secret_scan_report.json", "_t.txt", "_dbg.txt", "_dbg.py"}


def iter_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name in SKIP_FILES:
                continue
            yield os.path.join(dirpath, name)


def scan_text(text, compiled):
    hits = []
    for label, regex in compiled:
        for match in regex.finditer(text):
            hits.append({"pattern": label, "match": match.group(0)[:6] + "***"})
    return hits


def git_history_text(root):
    """git log -p --all 的原始字节，由调用方自己按 UTF-8 解码。"""
    return subprocess.run(
        ["git", "log", "-p", "--all"],
        cwd=root,
        capture_output=True,
        check=False,
    )


def scan_history(root, compiled):
    """返回 (hits, notes)。拿不到输出时 notes 说明原因，绝不静默算 0。"""
    proc = git_history_text(root)
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        return [], ["git log failed with exit %d: %s" % (proc.returncode, stderr[:200])]
    if proc.stdout is None:
        return [], ["git log produced no stdout stream"]
    text = proc.stdout.decode("utf-8", errors="replace")
    if not text.strip():
        return [], ["git log produced empty output"]
    return scan_text(text, compiled), []


def main() -> int:
    compiled = [(label, re.compile(pattern)) for label, pattern in PATTERNS]
    tree_hits = []
    for path in iter_files(ROOT):
        try:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
        except (UnicodeDecodeError, OSError):
            continue
        for hit in scan_text(text, compiled):
            hit["file"] = os.path.relpath(path, ROOT)
            tree_hits.append(hit)
    history_hits, notes = scan_history(ROOT, compiled)
    report = {
        "patterns": [label for label, _ in PATTERNS],
        "tree_hits": len(tree_hits),
        "history_hits": len(history_hits),
        "history_scan_notes": notes,
        "tree_details": tree_hits,
        "history_details": history_hits,
    }
    path = os.path.join(ROOT, "reports", "secret_scan_report.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(
        "patterns %d tree_hits %d history_hits %d history_scanned %s"
        % (len(PATTERNS), len(tree_hits), len(history_hits), "yes" if not notes else "no"),
    )
    for note in notes:
        print("history scan problem: %s" % note, file=sys.stderr)
    return 0 if (not tree_hits and not history_hits and not notes) else 1


if __name__ == "__main__":
    sys.exit(main())