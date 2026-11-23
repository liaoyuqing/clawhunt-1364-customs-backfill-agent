"""规则包加载。所有业务规则都来自 rules/*.yaml，代码里不写死任何比例或系数。"""

from __future__ import annotations

import os
from datetime import date
from typing import Any, Dict, List, Optional

import yaml

RULE_NAMES = (
    "bundle",
    "unit",
    "brand_words",
    "declare_price",
    "required_fields",
    "invoice_companies",
    "ledger_columns",
)

DEFAULT_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rules")


class ConfigError(RuntimeError):
    """规则包缺失、字段不全或版本未生效。"""


class Rules:
    """一次性加载七张规则表，并校验版本与生效日期。"""

    def __init__(self, root: Optional[str] = None, as_of: Optional[str] = None) -> None:
        self.root = str(root or os.environ.get("CLAWHUNT_1364_RULES") or DEFAULT_ROOT)
        self.as_of = as_of or date.today().isoformat()
        self._docs: Dict[str, Dict[str, Any]] = {}
        for name in RULE_NAMES:
            path = os.path.join(self.root, name + ".yaml")
            if not os.path.exists(path):
                raise ConfigError("rule file missing: %s" % path)
            with open(path, encoding="utf-8") as handle:
                doc = yaml.safe_load(handle)
            if not isinstance(doc, dict):
                raise ConfigError("rule file is not a mapping: %s" % path)
            for key in ("version", "effective_from"):
                if key not in doc:
                    raise ConfigError("%s missing %s" % (name, key))
            self._docs[name] = doc
        for name, doc in self._docs.items():
            if str(doc["effective_from"]) > str(self.as_of):
                raise ConfigError(
                    "rule %s effective from %s, requested as_of %s"
                    % (name, doc["effective_from"], self.as_of),
                )

    def doc(self, name: str) -> Dict[str, Any]:
        if name not in self._docs:
            raise ConfigError("unknown rule table: %s" % name)
        return self._docs[name]

    def version(self, name: str) -> str:
        return str(self.doc(name)["version"])

    def versions(self) -> Dict[str, str]:
        return {name: str(doc["version"]) for name, doc in self._docs.items()}

    @property
    def columns(self) -> List[str]:
        return list(self.doc("ledger_columns")["columns"])

    @property
    def source_kinds(self) -> List[str]:
        return list(self.doc("ledger_columns").get("source_kinds", []))

    @property
    def required(self) -> List[str]:
        return list(self.doc("required_fields")["required"])

    @property
    def hscode_pattern(self) -> str:
        return str(self.doc("required_fields")["hscode_pattern"])

    @property
    def reconciliation(self) -> List[Dict[str, Any]]:
        return list(self.doc("required_fields").get("reconciliation", []))

    @property
    def bundles(self) -> Dict[str, Any]:
        return dict(self.doc("bundle").get("bundles", {}))

    @property
    def unit_doc(self) -> Dict[str, Any]:
        return dict(self.doc("unit"))

    @property
    def unit_factors(self) -> Dict[str, Any]:
        """只要换算表本身，调用方不必再剥一层 factors。"""
        return dict(self.doc("unit").get("factors", {}))

    @property
    def brand_words(self) -> Dict[str, Any]:
        return dict(self.doc("brand_words"))

    @property
    def price_rules(self) -> Dict[str, Any]:
        return dict(self.doc("declare_price"))

    @property
    def invoice_companies(self) -> Dict[str, Any]:
        return dict(self.doc("invoice_companies"))