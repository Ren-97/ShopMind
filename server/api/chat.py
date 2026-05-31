"""POST /chat — SSE 主对话端点(§4.7)。

请求体:`{query: str, session_id: str}`
响应:`Content-Type: text/event-stream`,流式 yield 7 种事件(§4.7.1)。

Orchestrator 已经内置 catastrophic 兜底(§4.6.9 第 3 层),这里只做最外层
"启动失败"防御 — 例如 build_orchestrator 异常时,返回 error + done 给客户端。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sse_starlette.sse import EventSourceResponse

from server.agent.orchestrator import Orchestrator
from server.api.deps import get_current_user, get_orchestrator
from server.api.sse import event_to_sse_dict, make_done_sse, make_error_sse

log = structlog.get_logger("shopmind.api.chat")
router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000, description="用户当前 query")
    session_id: str | None = Field(
        default=None,
        description="可选,客户端持有的会话 ID;不传则后端生成 UUID",
    )


@router.post("/chat")
async def chat(
    req: ChatRequest,
    request: Request,
    user_id: Annotated[str, Depends(get_current_user)],
    orchestrator: Annotated[Orchestrator, Depends(get_orchestrator)],
) -> EventSourceResponse:
    """SSE 主对话。客户端断开 → orchestrator 协程被 cancel,优雅停止。"""
    session_id = req.session_id or f"sess-{uuid.uuid4().hex[:12]}"

    async def event_stream() -> AsyncIterator[dict[str, str]]:
        try:
            async for ev in orchestrator.handle_user_turn(
                user_query=req.query,
                user_id=user_id,
                session_id=session_id,
            ):
                if await request.is_disconnected():
                    log.info(
                        "chat_client_disconnected",
                        user_id=user_id,
                        session_id=session_id,
                    )
                    break
                yield event_to_sse_dict(ev)
        except Exception as e:  # noqa: BLE001 — 最外层 fallback,只兜启动阶段意外
            log.exception(
                "chat_startup_failure",
                user_id=user_id,
                session_id=session_id,
                error=str(e),
            )
            yield make_error_sse("internal_error", "系统暂时繁忙,请稍后再试。")
            yield make_done_sse(finish_reason="error")

    # ping=20:每 20s 一个 comment 心跳,前端 EventSource 自动忽略,防代理超时
    return EventSourceResponse(event_stream(), ping=20)


__all__ = ["router"]
