"""台账引擎：每格写入必须带 source 元数据，重复写与未知列都直接报错。"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Dict, Iterable, List, Optional

from .models import Cell, DuplicateWrite, Row, SourceRef, UnknownColumn


class Ledger:
    def __init__(self, columns: Iterable[str]) -> None:
        self.columns = list(columns)
        self._rows: "OrderedDict[str, Row]" = OrderedDict()

    def ensure_row(self, row_key: str, batch_no: Optional[str] = None) -> Row:
        row = self._rows.get(row_key)
        if row is None:
            row = Row(row_key=row_key, batch_no=str(batch_no or row_key))
            self._rows[row_key] = row
        return row

    def write(self, row_key: str, column: str, value: object, source: SourceRef) -> None:
        if column not in self.columns:
            raise UnknownColumn(column)
        row = self.ensure_row(row_key)
        if column in row.cells:
            raise DuplicateWrite("%s/%s already written" % (row_key, column))
        text = "" if value is None else str(value)
        row.cells[column] = Cell(column=column, value=text, source=source)

    def write_many(self, row_key: str, mapping: Dict[str, Any]) -> None:
        for column, payload in mapping.items():
            value, source = payload
            self.write(row_key, column, value, source)

    def row(self, row_key: str) -> Row:
        if row_key not in self._rows:
            raise KeyError("no such row: %s" % row_key)
        return self._rows[row_key]

    def rows(self) -> List[Row]:
        return list(self._rows.values())

    def keys(self) -> List[str]:
        return list(self._rows.keys())

    def to_dict(self) -> Dict[str, Any]:
        return {"columns": self.columns, "rows": [row.as_dict() for row in self.rows()]}

    def cells_missing_source(self) -> List[str]:
        out: List[str] = []
        for row in self.rows():
            for column in row.columns_without_source():
                out.append("%s/%s" % (row.row_key, column))
        return out