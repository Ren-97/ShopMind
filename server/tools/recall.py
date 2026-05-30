"""recall_history tool(§4.6.1 兜底)。

V1:ILIKE 子串 + 按时间倒排,跨 session 搜本用户历史消息。
V2:embed chat_history,走语义检索(留扩展点)。

仅在 session_state 漏抓 / 用户在追问历史时用(~5% 场景)。
"""

from __future__ import annotations

from typing import Any, ClassVar

import structlog
from pydantic import BaseModel, ConfigDict, Field

from server import config
from server.storage.user_repo import UserRepo
from server.tools.base import AgentDeps, Tool, ToolResult

log = structlog.get_logger("shopmind.tools.recall")


class RecallHistoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        description=(
            "要搜索的关键字 / 短语,跨 session 检索本用户历史消息。"
            "举例:'上次买的口红' / '推荐过的相机'。"
        )
    )
    top_n: int = Field(
        default=config.RECALL_HISTORY_TOP_N,
        ge=1,
        le=20,
        description="返回最近匹配的 N 条,默认 5。",
    )


class RecallHistoryTool(Tool):
    name: ClassVar[str] = "recall_history"
    description: ClassVar[str] = (
        "兜底:跨 session 搜本用户历史聊天记录。"
        "仅在用户在追问较早的对话内容、当前 session 上下文不够时调。"
        "**不要**用它代替 search_products 查商品事实。"
    )
    input_model: ClassVar[type[BaseModel]] = RecallHistoryInput

    async def _run(
        self,
        *,
        user_id: str,
        deps: AgentDeps,
        validated_input: BaseModel,
    ) -> ToolResult:
        assert isinstance(validated_input, RecallHistoryInput)
        async with deps.session_factory() as session:
            msgs = await UserRepo.search_messages(
                session,
                user_id,
                validated_input.query,
                top_n=validated_input.top_n,
            )

        items: list[dict[str, Any]] = [
            {
                "role": m.role,
                "content": m.content[:300],  # 截断防 LLM context 爆
                "session_id": m.session_id,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in msgs
        ]
        log.info("recall_done", user_id=user_id, query=validated_input.query, n_hits=len(items))
        return ToolResult(
            payload={"messages": items, "n_hits": len(items)}
        )


__all__ = ["RecallHistoryTool"]
