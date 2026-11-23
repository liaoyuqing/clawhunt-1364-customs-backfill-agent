"""密钥扫描：六类模式，扫工作区文件与 git 全历史，命中即非 0 退出。"""

from __future__ import annotations

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

SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache"}
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
    history_hits = []
    proc = subprocess.run(
        "git log -p --all",
        cwd=ROOT,
        capture_output=True,
        text=True,
        shell=True,
    )
    if proc.returncode == 0 and proc.stdout:
        history_hits = scan_text(proc.stdout, compiled)
    report = {
        "patterns": [label for label, _ in PATTERNS],
        "tree_hits": len(tree_hits),
        "history_hits": len(history_hits),
        "tree_details": tree_hits,
        "history_details": history_hits,
    }
    path = os.path.join(ROOT, "reports", "secret_scan_report.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    import json

    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print("patterns %d tree_hits %d history_hits %d" % (len(PATTERNS), len(tree_hits), len(history_hits)))
    return 0 if (not tree_hits and not history_hits) else 1


if __name__ == "__main__":
    sys.exit(main())