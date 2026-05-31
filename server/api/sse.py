"""SSE 序列化 helpers(§4.7.1 + §4.7.2)。

`AgentEvent` → `{"event": "<type>", "data": "<json>"}` 字典,直接喂给
sse-starlette 的 `EventSourceResponse`,框架自动按
    event: <type>\\ndata: <json>\\n\\n
SSE wire 格式发送。
"""

from __future__ import annotations

import json
from typing import Any

from server.agent.orchestrator import AgentEvent


def event_to_sse_dict(event: AgentEvent) -> dict[str, str]:
    """AgentEvent → sse-starlette 喂的 `{event, data}` dict(data 是 JSON 字符串)。"""
    return {
        "event": event.type,
        "data": json.dumps(event.data, ensure_ascii=False, default=str),
    }


def make_error_sse(code: str, msg: str) -> dict[str, str]:
    """构造 error event 给 catastrophic 兜底用(§4.6.9 第 4 层)。"""
    return {
        "event": "error",
        "data": json.dumps({"code": code, "msg": msg}, ensure_ascii=False),
    }


def make_done_sse(finish_reason: str = "error") -> dict[str, str]:
    return {
        "event": "done",
        "data": json.dumps({"finish_reason": finish_reason}, ensure_ascii=False),
    }


def raw_event(event: str, data: dict[str, Any]) -> dict[str, str]:
    """裸构造任意 event(测试 / 心跳用)。"""
    return {"event": event, "data": json.dumps(data, ensure_ascii=False, default=str)}


__all__ = [
    "event_to_sse_dict",
    "make_done_sse",
    "make_error_sse",
    "raw_event",
]
