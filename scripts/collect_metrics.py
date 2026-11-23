"""实测数字采集：全部数字在这里现算，正文里的数字一律照抄本文件输出。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(cmd):
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, shell=isinstance(cmd, str))
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def count_lines(paths):
    total = 0
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            total += sum(1 for _ in handle)
    return total


def tracked_files():
    code, out = run("git ls-files")
    if code != 0:
        return []
    return [line for line in out.splitlines() if line.strip()]


def py_files(folder):
    out = []
    for dirpath, _dirnames, filenames in os.walk(os.path.join(ROOT, folder)):
        for name in sorted(filenames):
            if name.endswith(".py"):
                out.append(os.path.join(dirpath, name))
    return sorted(out)


VOLATILE = {"reports/metrics.json"}


def tree_hash(paths):
    """对 tracked 文件求树哈希；metrics.json 自身随每次采集变化，排除在外。"""
    digest = hashlib.sha256()
    for rel in sorted(paths):
        if rel in VOLATILE:
            continue
        full = os.path.join(ROOT, rel)
        if not os.path.exists(full):
            continue
        digest.update(rel.encode("utf-8"))
        with open(full, "rb") as handle:
            digest.update(hashlib.sha256(handle.read()).digest())
    return digest.hexdigest()


def main() -> int:
    code, pytest_out = run([
        os.path.join(ROOT, ".venv", "Scripts", "python.exe"),
        "-m", "pytest", "-o", "addopts=", "-q",
    ])
    test_files = sorted(
        name for name in os.listdir(os.path.join(ROOT, "tests")) if name.startswith("test_") and name.endswith(".py")
    )
    collect_code, collect_out = run([
        os.path.join(ROOT, ".venv", "Scripts", "python.exe"),
        "-m", "pytest", "-o", "addopts=", "--collect-only", "-q",
    ])
    test_cases = 0
    for line in collect_out.splitlines():
        match = re.match(r"^(\d+)\s+tests?\s+collected", line.strip())
        if match:
            test_cases = int(match.group(1))
    if not test_cases:
        test_cases = sum(1 for line in collect_out.splitlines() if "::" in line)
    core_files = py_files("core")
    script_files = py_files("scripts")
    test_paths = [os.path.join("tests", name) for name in test_files]
    with open(os.path.join(ROOT, "reports", "sample50_report.json"), encoding="utf-8") as handle:
        sample = json.load(handle)
    with open(os.path.join(ROOT, "rules", "ledger_columns.yaml"), encoding="utf-8") as handle:
        pass
    import yaml

    rule_versions = {}
    for name in ("bundle", "unit", "brand_words", "declare_price", "required_fields", "invoice_companies", "ledger_columns"):
        with open(os.path.join(ROOT, "rules", name + ".yaml"), encoding="utf-8") as handle:
            rule_versions[name] = str(yaml.safe_load(handle)["version"])
    columns = None
    with open(os.path.join(ROOT, "rules", "ledger_columns.yaml"), encoding="utf-8") as handle:
        columns = yaml.safe_load(handle)["columns"]
    batches = json.load(open(os.path.join(ROOT, "fixtures", "lingxing", "batches.json"), encoding="utf-8"))
    products = json.load(open(os.path.join(ROOT, "fixtures", "lingxing", "products.json"), encoding="utf-8"))
    suppliers = json.load(open(os.path.join(ROOT, "fixtures", "lingxing", "suppliers.json"), encoding="utf-8"))
    files = tracked_files()
    passed_match = re.search(r"(\d+)\s+passed", pytest_out)
    passed = int(passed_match.group(1)) if passed_match else 0
    metrics = {
        "python_version": sys.version.split()[0],
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pytest_exit_code": code,
        "pytest_tail": pytest_out.strip().splitlines()[-1] if pytest_out.strip() else "",
        "test_files": len(test_files),
        "test_cases_collected": test_cases,
        "test_cases_passed": passed,
        "core_py_files": len(core_files),
        "core_loc": count_lines(core_files),
        "scripts_py_files": len(script_files),
        "scripts_loc": count_lines(script_files),
        "tests_loc": count_lines([os.path.join(ROOT, p) for p in test_paths]),
        "rule_tables": len(rule_versions),
        "rule_versions": rule_versions,
        "ledger_columns": len(columns),
        "fixture_batches": len(batches),
        "fixture_products": len(products),
        "fixture_suppliers": len(suppliers),
        "fixture_lines": sum(len(b["lines"]) for b in batches),
        "sample_rows": sample["rows"],
        "sample_cells_compared": sample["cells_compared"],
        "sample_cells_consistent": sample["cells_consistent"],
        "sample_cells_undecidable": sample["cells_undecidable"],
        "sample_consistency_rate": sample["consistency_rate"],
        "sample_source_kind_missing": sample["source_kind_missing"],
        "sample_differences": sample["difference_count"],
        "sample_passes_threshold": sample["passes_threshold"],
        "tracked_files": len(files),
        "tree_sha256": tree_hash(files),
    }
    out_dir = os.path.join(ROOT, "reports")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "metrics.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(metrics, handle, ensure_ascii=False, indent=2, sort_keys=True)
    print(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True))
    print("metrics -> %s" % path)
    return 0 if code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())