"""Computer Use 界面操作：截图 -> 动作描述 -> 执行器接口，全部走真实 HTTP 调用。

执行器地址与 key 只从环境变量读；动作与截图的对应关系由调用方给出。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .http_client import Transport, UrllibTransport, json_request
from .vision_client import data_uri

ENV_BASE_URL = 'COMPUTER_USE_BASE_URL'
ENV_API_KEY = 'COMPUTER_USE_API_KEY'
ENV_MODEL = 'COMPUTER_USE_MODEL'

DEFAULT_MODEL = 'computer-use-latest'

ACTION_KINDS = ('screenshot', 'click', 'type', 'scroll')


class ComputerUseConfigError(RuntimeError):
    """界面操作执行器配置缺失。"""


class ComputerUseCallError(RuntimeError):
    """界面操作执行器返回了无法解析的响应。"""


@dataclass
class ComputerUseConfig:
    """执行器连接参数，全部来自环境变量。"""

    base_url: str
    api_key: str = ''
    model: str = DEFAULT_MODEL
    timeout: float = 60.0

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> 'ComputerUseConfig':
        source = env if env is not None else os.environ
        base_url = str(source.get(ENV_BASE_URL, '')).strip().rstrip('/')
        if not base_url:
            raise ComputerUseConfigError('missing environment variable %s' % ENV_BASE_URL)
        return cls(
            base_url=base_url,
            api_key=str(source.get(ENV_API_KEY, '')).strip(),
            model=str(source.get(ENV_MODEL, DEFAULT_MODEL)).strip() or DEFAULT_MODEL,
        )

    def url(self) -> str:
        return '%s/v1/computer/actions' % self.base_url


@dataclass
class ComputerAction:
    """一次界面动作：kind 决定动作类型，target/text/coordinate 描述动作参数。"""

    kind: str
    target: str = ''
    text: str = ''
    coordinate: Optional[Tuple[int, int]] = None
    screenshot_id: str = ''

    def as_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            'kind': str(self.kind),
            'target': str(self.target),
            'text': str(self.text),
        }
        if self.coordinate is not None:
            out['coordinate'] = [int(self.coordinate[0]), int(self.coordinate[1])]
        if self.screenshot_id:
            out['screenshot_id'] = str(self.screenshot_id)
        return out


@dataclass
class Screenshot:
    """一张界面截图；image_bytes 可为空，此时只用 screenshot_id 引用远端图片。"""

    screenshot_id: str
    image_bytes: bytes = b''
    width: int = 0
    height: int = 0

    def to_data_uri(self, mime: str = 'image/png') -> str:
        if not self.image_bytes:
            return ''
        return data_uri(self.image_bytes, mime=mime)


@dataclass
class ComputerUseClient:
    """真实界面操作客户端；transport 可注入，测试能记录发出的 HTTP 请求。"""

    config: ComputerUseConfig
    http: Transport = field(default_factory=UrllibTransport)
    requests: List[Dict[str, Any]] = field(default_factory=list)

    def _headers(self) -> Dict[str, str]:
        headers = {'Content-Type': 'application/json'}
        if self.config.api_key:
            headers['Authorization'] = 'Bearer %s' % self.config.api_key
        return headers

    def execute(self, action: ComputerAction, screenshot: Optional[Screenshot] = None) -> Dict[str, Any]:
        """把一个动作发给执行器；截图以 data URI 形式随请求体一起送出去。"""
        if str(action.kind) not in ACTION_KINDS:
            raise ComputerUseCallError('未知动作类型：%s' % action.kind)
        payload: Dict[str, Any] = {
            'model': self.config.model,
            'action': action.as_dict(),
        }
        if screenshot is not None:
            uri = screenshot.to_data_uri()
            payload['screenshot'] = uri if uri else str(screenshot.screenshot_id)
            payload['screenshot_id'] = str(screenshot.screenshot_id)
        response = json_request(self.http, 'POST', self.config.url(), headers=self._headers(), payload=payload, timeout=self.config.timeout)
        body = response.json()
        self.requests.append({'url': self.config.url(), 'method': 'POST', 'kind': str(action.kind), 'body_keys': sorted(payload.keys())})
        if not isinstance(body, dict):
            raise ComputerUseCallError('界面操作执行器返回的不是对象')
        return dict(body)


def plan_actions(vision_result: Dict[str, Any], threshold: float = 0.85) -> Tuple[List[ComputerAction], List[Dict[str, Any]]]:
    """按置信度分拣视觉结果：高置信度生成动作，低置信度进人工队列。"""
    actions: List[ComputerAction] = []
    manual: List[Dict[str, Any]] = []
    screenshot_id = str(vision_result.get('screenshot_id', ''))
    for name, spec in (vision_result.get('fields') or {}).items():
        if isinstance(spec, str):
            spec = {'text': spec, 'confidence': 1.0}
        confidence = float(spec.get('confidence', 0.0))
        text = str(spec.get('text', ''))
        if confidence < float(threshold):
            manual.append({
                'name': str(name),
                'text': text,
                'confidence': confidence,
                'screenshot_id': screenshot_id,
                'reason': '置信度 %.2f 低于阈值 %.2f，转人工确认' % (confidence, float(threshold)),
            })
            continue
        actions.append(ComputerAction(kind='type', target=str(name), text=text, screenshot_id=screenshot_id))
    return actions, manual
