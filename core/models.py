"""数据模型：格子、来源引用、问题与差异。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

SOURCE_KINDS = ("api", "vision", "manual", "config")


@dataclass(frozen=True)
class SourceRef:
    """一格数据的出处。kind 只能是 api / vision / manual / config。"""

    kind: str
    ref: str
    at: str

    def __post_init__(self) -> None:
        if self.kind not in SOURCE_KINDS:
            raise ValueError("unknown source kind: %r" % (self.kind,))
        if not self.ref:
            raise ValueError("source ref must not be empty")

    def as_dict(self) -> Dict[str, str]:
        return {"kind": self.kind, "ref": self.ref, "at": self.at}


@dataclass(frozen=True)
class Cell:
    column: str
    value: str
    source: SourceRef

    def as_dict(self) -> Dict[str, Any]:
        return {"value": self.value, "source": self.source.as_dict()}


class UnknownColumn(KeyError):
    """写入的列不在台账列定义内。"""


class DuplicateWrite(RuntimeError):
    """同一行同一列被写第二次。"""


@dataclass
class Row:
    row_key: str
    batch_no: str
    cells: Dict[str, Cell] = field(default_factory=dict)

    def get(self, column: str) -> Optional[Cell]:
        return self.cells.get(column)

    def value(self, column: str) -> str:
        cell = self.cells.get(column)
        return cell.value if cell is not None else ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "row_key": self.row_key,
            "batch_no": self.batch_no,
            "cells": {c: cell.as_dict() for c, cell in self.cells.items()},
        }

    def columns_without_source(self) -> List[str]:
        return [c for c, cell in self.cells.items() if not cell.source.kind]


@dataclass(frozen=True)
class Issue:
    code: str
    batch_no: str
    column: str
    message: str

    def as_dict(self) -> Dict[str, str]:
        return {
            "code": self.code,
            "batch_no": self.batch_no,
            "column": self.column,
            "message": self.message,
        }


@dataclass(frozen=True)
class Difference:
    field: str
    expected: str
    actual: str
    batch_no: str = ""
    kind: str = "mismatch"

    def as_dict(self) -> Dict[str, str]:
        return {
            "field": self.field,
            "expected": self.expected,
            "actual": self.actual,
            "batch_no": self.batch_no,
            "kind": self.kind,
        }