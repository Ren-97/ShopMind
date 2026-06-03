"""POST /chat — SSE 主对话端点(§4.7)。

请求体:`{query: str, session_id: str}`
响应:`Content-Type: text/event-stream`,流式 yield 7 种事件(§4.7.1)。

Orchestrator 已经内置 catastrophic 兜底(§4.6.9 第 3 层),这里只做最外层
"启动失败"防御 — 例如 build_orchestrator 异常时,返回 error + done 给客户端。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sse_starlette.sse import EventSourceResponse

from server.agent.orchestrator import Orchestrator
from server.api.deps import get_current_user, get_orchestrator, get_session_factory
from server.api.sse import event_to_sse_dict, make_done_sse, make_error_sse
from server.storage.user_repo import UserRepo

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


# ──────────────────────────────────────────────────────────────
# B+:对话历史 — 持久化 chat_history,前端启动延续 + 🔄 一键清空
# ──────────────────────────────────────────────────────────────
@router.get("/chat/history")
async def get_chat_history(
    user_id: Annotated[str, Depends(get_current_user)],
    session_factory: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_session_factory)
    ],
    session_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """拉当前 user 的对话历史。

    - `session_id` 可选:不传则拉这个 user **所有 session** 的消息(单 session 心智下 = 全部)
    - 仅返回 user / assistant 两个 role(不含 tool result 之类的内部事件)
    - card_refs 跟着每条 assistant message 一起回:`{products: [...], compare: [...], order: "..."}`
    - 前端拿到后并发 GET /product/{id} + /order/{id} 实时拼最新卡片(B+ 落地做法)
    """
    async with session_factory() as session:
        messages = await UserRepo.list_messages(
            session, user_id=user_id, session_id=session_id, limit=limit
        ) if session_id else await _list_all_history(session, user_id, limit)

    return {
        "messages": [
            {
                "msg_id": m.msg_id,
                "session_id": m.session_id,
                "role": m.role,
                "content": m.content,
                "card_refs": m.card_refs,
                "created_at": (
                    m.created_at.isoformat() + "Z" if m.created_at else None
                ),
            }
            for m in messages
            if m.role in ("user", "assistant")
        ]
    }


@router.delete("/chat/history", status_code=200)
async def delete_chat_history(
    user_id: Annotated[str, Depends(get_current_user)],
    session_factory: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_session_factory)
    ],
    session_id: str | None = None,
) -> dict[str, Any]:
    """🔄 清空对话记录 — 硬删 DB(不可恢复,跟 ChatGPT / 微信清空一致)。

    - `session_id` 可选:不传则清这个 user 的所有 chat_history
    - 返回删除的行数(用于客户端 toast 反馈)
    """
    async with session_factory() as session:
        deleted = await UserRepo.clear_chat_history(
            session, user_id=user_id, session_id=session_id
        )
        await session.commit()
    log.info(
        "chat_history_cleared",
        user_id=user_id,
        session_id=session_id,
        deleted=deleted,
    )
    return {"deleted": deleted}


async def _list_all_history(
    session: AsyncSession, user_id: str, limit: int
) -> list[Any]:
    """单 session 心智下,client 不传 session_id 也能拉全部历史 — 按时间正序。"""
    from sqlalchemy import select
    from server.storage.models import ChatHistory

    stmt = (
        select(ChatHistory)
        .where(ChatHistory.user_id == user_id)
        .order_by(ChatHistory.created_at, ChatHistory.msg_id)
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


__all__ = ["router"]
