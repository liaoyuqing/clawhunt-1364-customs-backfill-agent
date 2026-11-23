"""共用夹具：规则包、50 批次源数据、内存台账（含模块四 7 列）。"""

from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import pipeline  # noqa: E402
from core.config import Rules  # noqa: E402
from core.declare import BatchContext, BatchFiller  # noqa: E402
from core.ledger import Ledger  # noqa: E402

FIX = os.path.join(ROOT, "fixtures")


def load_json(*parts):
    with open(os.path.join(FIX, *parts), encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="session")
def rules():
    return Rules()


@pytest.fixture(scope="session")
def batches():
    return load_json("lingxing", "batches.json")


@pytest.fixture(scope="session")
def products():
    return {item["sku"]: item for item in load_json("lingxing", "products.json")}


@pytest.fixture(scope="session")
def suppliers():
    return {item["id"]: item for item in load_json("lingxing", "suppliers.json")}


@pytest.fixture(scope="session")
def invoices(rules):
    return pipeline.load_invoices(os.path.join(FIX, "invoices", "ledger.json"))


@pytest.fixture(scope="session")
def filled(rules, batches, products, suppliers, invoices):
    """走完整链路：模块二 16 列 + 模块四 7 列，与 core.cli 同一条流水线。"""
    result = pipeline.run_pipeline(rules, batches, products, suppliers, invoices)
    return result["ledger"], result["fills"]


@pytest.fixture(scope="session")
def pipeline_result(rules, batches, products, suppliers, invoices):
    return pipeline.run_pipeline(rules, batches, products, suppliers, invoices)


@pytest.fixture(scope="session")
def expected_bundle():
    return load_json("expected.json")