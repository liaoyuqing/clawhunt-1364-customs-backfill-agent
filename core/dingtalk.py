"""钉钉在线表格适配层：列序校验、批量 upsert、按批次号定位行。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .models import Row, SourceRef


class ColumnMismatch(RuntimeError):
    """远端表头与规则包的列定义不一致。"""


class DingTalkSheet:
    """内存版表格，接口形状与钉钉开放平台 batchUpdate 对齐。"""

    def __init__(self, columns: List[str], header: Optional[List[str]] = None) -> None:
        self.columns = list(columns)
        header = list(header if header is not None else columns)
        if header != self.columns:
            raise ColumnMismatch("sheet header %r != rules columns %r" % (header, self.columns))
        self._rows: Dict[str, Dict[str, Any]] = {}
        self.writes: List[Dict[str, Any]] = []

    def key_for_batch(self, batch_no: str) -> str:
        return "batch:%s" % batch_no

    def upsert_row(self, row_key: str) -> None:
        self._rows.setdefault(row_key, {})

    def write_cells(self, row_key: str, cells: Dict[str, str], row: Row) -> None:
        target = self._rows.setdefault(row_key, {})
        for column, value in cells.items():
            if column not in self.columns:
                raise ColumnMismatch("column %r not in sheet header" % (column,))
            target[column] = value
        self.writes.append({"row_key": row_key, "count": len(cells)})

    def read_row(self, row_key: str) -> Dict[str, Any]:
        return dict(self._rows.get(row_key, {}))

    def rows(self) -> Dict[str, Dict[str, Any]]:
        return {key: dict(value) for key, value in self._rows.items()}

    def link_for(self, batch_no: str) -> str:
        """归档共享链接：钉钉表格行内超链接。"""
        return "https://alidocs.dingtalk.com/i/nodes/%s" % batch_no