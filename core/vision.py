"""视觉抽取：接口覆盖不到的场景走这里，输出必带 bbox 与置信度。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .models import SourceRef

VISION_THRESHOLD = 0.85


@dataclass(frozen=True)
class Span:
    text: str
    bbox: List[int]
    confidence: float

    def as_dict(self) -> Dict[str, Any]:
        return {"text": self.text, "bbox": list(self.bbox), "confidence": self.confidence}


@dataclass(frozen=True)
class VisionField:
    name: str
    text: str
    bbox: List[int]
    confidence: float
    screenshot_id: str

    @property
    def needs_manual(self) -> bool:
        return self.confidence < VISION_THRESHOLD

    def source(self, at: str) -> SourceRef:
        return SourceRef(kind="vision", ref=self.screenshot_id, at=at)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "text": self.text,
            "bbox": list(self.bbox),
            "confidence": self.confidence,
            "screenshot_id": self.screenshot_id,
            "needs_manual": self.needs_manual,
        }


class ScreenshotSource:
    """读取预置的截图识别结果（fixtures/vision/*.json），不联网。"""

    def __init__(self, payload: Dict[str, Any]) -> None:
        self.screenshot_id = str(payload["screenshot_id"])
        self.fields: List[VisionField] = []
        for item in payload.get("fields", []):
            self.fields.append(
                VisionField(
                    name=str(item["name"]),
                    text=str(item["text"]),
                    bbox=list(item["bbox"]),
                    confidence=float(item["confidence"]),
                    screenshot_id=self.screenshot_id,
                )
            )

    def field(self, name: str) -> Optional[VisionField]:
        for field in self.fields:
            if field.name == name:
                return field
        return None

    def manual_queue(self) -> List[Dict[str, Any]]:
        return [f.as_dict() for f in self.fields if f.needs_manual]



def apply_vision_fields(ledger, row_key, fields, at=""):
    """把视觉识别结果写进指定明细行。

    fields 是 core.vision_client.to_vision_fields 的输出（字典列表，含
    name / text / confidence / needs_manual）。高置信度字段写台账，来源标记
    为 vision；低置信度字段不写，落到返回的 manual 列表交给人工。
    已被占用的格不覆盖，避免触发 DuplicateWrite。
    """
    from .models import SourceRef

    written = []
    manual = []
    row = ledger.row(row_key)
    for field in fields:
        name = str(field.get("name", ""))
        if name not in ledger.columns:
            continue
        if row.get(name) is not None:
            continue
        if bool(field.get("needs_manual")):
            manual.append(dict(field))
            continue
        shot = str(field.get("screenshot_id", ""))
        ledger.write(row_key, name, str(field.get("text", "")), SourceRef(kind="vision", ref=shot, at=at))
        written.append(dict(field))
    return written, manual
