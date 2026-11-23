"""模块四本地模型调用：OpenAI 兼容的本地服务，地址与 key 只从环境变量读。

发票原件识别必须先过 core.preflight 的内网地址校验，没过就一次请求都不发。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .http_client import Transport, UrllibTransport, json_request
from .preflight import check_egress_whitelist, check_model_base_url

ENV_BASE_URL = 'LOCAL_MODEL_BASE_URL'
ENV_API_KEY = 'LOCAL_MODEL_API_KEY'
ENV_MODEL = 'LOCAL_MODEL_MODEL'

DEFAULT_MODEL = 'local-vision-invoice'

INVOICE_FIELDS = ('invoice_no', 'issued_at', 'buyer_name', 'buyer_tax_no', 'amount', 'invoice_type')

DEFAULT_PROMPT = (
    'Read the invoice in this picture. Answer with JSON only, keys: '
    'invoice_no, issued_at, buyer_name, buyer_tax_no, amount, invoice_type.'
)


class LocalModelConfigError(RuntimeError):
    """本地模型配置缺失。"""


class LocalModelBlocked(RuntimeError):
    """地址没通过内网白名单校验，调用被拒。"""

    def __init__(self, message: str, problems: Optional[Sequence[str]] = None) -> None:
        super().__init__(message)
        self.problems = list(problems or [])


class LocalModelCallError(RuntimeError):
    """本地模型返回了无法解析的响应。"""


@dataclass
class LocalModelConfig:
    """本地模型连接参数，全部来自环境变量。"""

    base_url: str
    api_key: str = ''
    model: str = DEFAULT_MODEL
    timeout: float = 60.0

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> 'LocalModelConfig':
        source = env if env is not None else os.environ
        base_url = str(source.get(ENV_BASE_URL, '')).strip().rstrip('/')
        if not base_url:
            raise LocalModelConfigError('missing environment variable %s' % ENV_BASE_URL)
        return cls(
            base_url=base_url,
            api_key=str(source.get(ENV_API_KEY, '')).strip(),
            model=str(source.get(ENV_MODEL, DEFAULT_MODEL)).strip() or DEFAULT_MODEL,
        )

    def url(self) -> str:
        return '%s/v1/chat/completions' % self.base_url


def assert_local_allowed(config: LocalModelConfig, egress_whitelist: Optional[Sequence[str]] = None) -> List[str]:
    """内网校验：模型地址必须是回环或私有段；给了白名单时白名单也必须全是私有段。"""
    problems = list(check_model_base_url(config.base_url))
    if egress_whitelist is not None:
        problems.extend(check_egress_whitelist(list(egress_whitelist)))
    if problems:
        raise LocalModelBlocked('本地模型地址未通过内网校验：%s' % '；'.join(problems), problems)
    return problems


def parse_invoice(content: str) -> Dict[str, str]:
    """从模型回复里解析发票字段，六个字段缺一不可。"""
    text = str(content or '').strip()
    if text.startswith('```'):
        text = text.strip('`')
        if text.lower().startswith('json'):
            text = text[4:]
    parsed: Any = None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find('{')
        end = text.rfind('}')
        if start >= 0 and end > start:
            try:
                parsed = json.loads(text[start:end + 1])
            except json.JSONDecodeError as exc:
                raise LocalModelCallError('本地模型回复不是 JSON：%s' % exc)
    if not isinstance(parsed, dict):
        raise LocalModelCallError('本地模型回复不是 JSON 对象')
    out: Dict[str, str] = {}
    for key in INVOICE_FIELDS:
        value = parsed.get(key, '')
        if isinstance(value, dict):
            value = value.get('text', '')
        out[key] = str(value).strip()
    missing = [key for key in INVOICE_FIELDS if not out[key]]
    if missing:
        raise LocalModelCallError('本地模型回复缺字段：%s' % '、'.join(missing))
    return out


@dataclass
class LocalModelClient:
    """真实本地模型客户端；transport 可注入，测试能记录发出的 HTTP 请求。"""

    config: LocalModelConfig
    http: Transport = field(default_factory=UrllibTransport)
    requests: List[Dict[str, Any]] = field(default_factory=list)

    def _headers(self) -> Dict[str, str]:
        headers = {'Content-Type': 'application/json'}
        if self.config.api_key:
            headers['Authorization'] = 'Bearer %s' % self.config.api_key
        return headers

    def recognize_invoice(self, image_url: str, prompt: str = '') -> Dict[str, str]:
        """识别一张发票原件；地址没过内网校验就直接拒，连请求都不发。"""
        assert_local_allowed(self.config)
        question = prompt or DEFAULT_PROMPT
        payload = {
            'model': self.config.model,
            'temperature': 0,
            'messages': [
                {
                    'role': 'user',
                    'content': [
                        {'type': 'text', 'text': question},
                        {'type': 'image_url', 'image_url': {'url': image_url}},
                    ],
                }
            ],
        }
        response = json_request(self.http, 'POST', self.config.url(), headers=self._headers(), payload=payload, timeout=self.config.timeout)
        body = response.json()
        self.requests.append({'url': self.config.url(), 'method': 'POST', 'model': self.config.model, 'body_keys': sorted(payload.keys())})
        choices = body.get('choices') or []
        if not choices:
            raise LocalModelCallError('本地模型回复没有 choices')
        message = choices[0].get('message') or {}
        return parse_invoice(message.get('content', ''))
