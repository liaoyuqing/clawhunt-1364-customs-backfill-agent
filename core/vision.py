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