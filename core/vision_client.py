"""真实视觉模型调用：OpenAI 兼容的 chat/completions，地址与 key 只从环境变量读。

请求体带 image_url（base64 data URI 或平台 file id），响应按结构化 JSON 解析出
SKU / 规格 / 数量 / 置信度。置信度低于阈值（可配）时字段不写台账，转人工队列。
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any, Dict, List, Optional

from .http_client import HttpError, Transport, UrllibTransport, json_request

ENV_BASE_URL = 'VISION_BASE_URL'
ENV_API_KEY = 'VISION_API_KEY'
ENV_MODEL = 'VISION_MODEL'
ENV_THRESHOLD = 'VISION_THRESHOLD'

DEFAULT_THRESHOLD = 0.85
DEFAULT_MODEL = 'vision-latest'


class VisionConfigError(RuntimeError):
    """视觉服务配置缺失。"""


class VisionCallError(RuntimeError):
    """视觉服务返回了无法解析的响应。"""


class VisionConfig:
    """视觉服务连接参数，全部来自环境变量。"""

    def __init__(
        self,
        base_url: str,
        api_key: str = '',
        model: str = DEFAULT_MODEL,
        threshold: float = DEFAULT_THRESHOLD,
        timeout: float = 60.0,
    ) -> None:
        self.base_url = str(base_url).rstrip('/')
        self.api_key = str(api_key)
        self.model = str(model)
        self.threshold = float(threshold)
        self.timeout = float(timeout)

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> 'VisionConfig':
        source = env if env is not None else os.environ
        base_url = str(source.get(ENV_BASE_URL, '')).strip()
        if not base_url:
            raise VisionConfigError('missing environment variable %s' % ENV_BASE_URL)
        raw_threshold = str(source.get(ENV_THRESHOLD, '')).strip()
        threshold = DEFAULT_THRESHOLD
        if raw_threshold:
            try:
                threshold = float(raw_threshold)
            except ValueError as exc:
                raise VisionConfigError('%s is not a number: %s' % (ENV_THRESHOLD, raw_threshold)) from exc
        return cls(
            base_url=base_url,
            api_key=str(source.get(ENV_API_KEY, '')).strip(),
            model=str(source.get(ENV_MODEL, DEFAULT_MODEL)).strip() or DEFAULT_MODEL,
            threshold=threshold,
        )

    def url(self) -> str:
        return '%s/v1/chat/completions' % self.base_url


def data_uri(image_bytes: bytes, mime: str = 'image/png') -> str:
    """把图片字节变成 image_url 能直接用的 data URI。"""
    encoded = base64.b64encode(image_bytes).decode('ascii')
    return 'data:%s;base64,%s' % (mime, encoded)


DEFAULT_PROMPT = (
    'Read the product label in this picture. Answer with JSON only: '
    '{"fields": {"SKU": {"text": "", "confidence": 0}, '
    '"规格": {"text": "", "confidence": 0}, '
    '"数量": {"text": "", "confidence": 0}}}'
)


def build_payload(image_url: str, prompt: str, model: str) -> Dict[str, Any]:
    """OpenAI 兼容的请求体：一条 user 消息里带图片与提问。"""
    return {
        'model': model,
        'temperature': 0,
        'messages': [
            {
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': prompt},
                    {'type': 'image_url', 'image_url': {'url': image_url}},
                ],
            }
        ],
    }


def parse_fields(content: str) -> Dict[str, Any]:
    """从模型回复里解析结构化字段：fields -> 名称 -> {text, confidence}。"""
    text = str(content or '').strip()
    if text.startswith('```'):
        text = text.strip('`')
        if text.lower().startswith('json'):
            text = text[4:]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find('{')
        end = text.rfind('}')
        if start < 0 or end <= start:
            raise VisionCallError('model reply is not JSON: %s' % text[:120])
        try:
            parsed = json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            raise VisionCallError('model reply is not JSON: %s' % exc)
    fields = parsed.get('fields') if isinstance(parsed, dict) else None
    if not isinstance(fields, dict):
        raise VisionCallError('model reply has no fields object')
    return {'fields': fields, 'raw': parsed}


class VisionClient:
    """真实视觉调用客户端；transport 可注入，测试能记录发出的 HTTP 请求。"""

    def __init__(self, config: VisionConfig, http: Optional[Transport] = None) -> None:
        self.config = config
        self.http = http or UrllibTransport()
        self.requests: List[Dict[str, Any]] = []

    def _headers(self) -> Dict[str, str]:
        headers = {'Content-Type': 'application/json'}
        if self.config.api_key:
            headers['Authorization'] = 'Bearer %s' % self.config.api_key
        return headers

    def recognize(self, image_url: str, prompt: str = '') -> Dict[str, Any]:
        """发一次 chat/completions 并返回结构化字段。"""
        question = prompt or DEFAULT_PROMPT
        payload = build_payload(image_url, question, self.config.model)
        response = json_request(self.http, 'POST', self.config.url(), headers=self._headers(), payload=payload, timeout=self.config.timeout)
        body = response.json()
        self.requests.append({'url': self.config.url(), 'method': 'POST', 'model': self.config.model, 'body_keys': sorted(payload.keys())})
        choices = body.get('choices') or []
        if not choices:
            raise VisionCallError('vision reply has no choices')
        message = choices[0].get('message') or {}
        content = message.get('content', '')
        return parse_fields(content)


def to_vision_fields(result: Dict[str, Any], screenshot_id: str, threshold: float) -> List[Dict[str, Any]]:
    """把模型返回的字段转成 core.vision.VisionField 形状（含 needs_manual）。"""
    out: List[Dict[str, Any]] = []
    for name, spec in (result.get('fields') or {}).items():
        if isinstance(spec, str):
            spec = {'text': spec, 'confidence': 1.0}
        confidence = float(spec.get('confidence', 0.0))
        out.append(
            {
                'name': str(name),
                'text': str(spec.get('text', '')),
                'bbox': list(spec.get('bbox', [])),
                'confidence': confidence,
                'screenshot_id': str(screenshot_id),
                'needs_manual': confidence < float(threshold),
            }
        )
    return out
