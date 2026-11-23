"""品名清洗与归并：去品牌词之后再比，避免同物异名被当成新品。"""

from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Optional, Tuple


def normalize(text: str, cfg: Dict) -> str:
    """全角转半角、折叠空白。"""
    mode = cfg.get("normalize", {}) or {}
    out = unicodedata.normalize("NFKC", text) if mode.get("fullwidth_to_halfwidth", True) else text
    if mode.get("spaces", True):
        out = re.sub(r"\s+", " ", out)
    return out.strip()


def strip_brand_words(text: str, cfg: Dict) -> str:
    """按配置表剔掉品牌词，ASCII 词忽略大小写。"""
    out = text
    for word in cfg.get("words", []) or []:
        out = re.sub(re.escape(str(word)), "", out, flags=re.IGNORECASE)
    out = re.sub(r"\s+", " ", out).strip()
    return out.strip(" -_")


def merge_key(text: str, cfg: Dict) -> str:
    """归并用的比较键：清洗后统一小写。"""
    return strip_brand_words(normalize(text, cfg), cfg).casefold()


class NameIndex:
    """品名到标准编码的索引，冲突时保留首个并记录下来。"""

    def __init__(self, cfg: Dict) -> None:
        self._cfg = cfg
        self._map: Dict[str, str] = {}
        self._names: Dict[str, str] = {}
        self.conflicts: List[Tuple[str, str, str]] = []

    def add(self, name: str, code: str) -> bool:
        key = merge_key(name, self._cfg)
        if key in self._map:
            if self._map[key] != code:
                self.conflicts.append((name, self._map[key], code))
                return False
            return True
        self._map[key] = code
        self._names[key] = name
        return True

    def find(self, name: str) -> Optional[str]:
        return self._map.get(merge_key(name, self._cfg))

    def __len__(self) -> int:
        return len(self._map)