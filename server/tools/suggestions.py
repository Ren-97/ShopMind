"""show_suggestions tool(§4.6.11)。

特殊 tool:无业务副作用,只产生 SSE `event: suggestions`(由 orchestrator 转发)。
Agent 应在推荐 / 对比 / 购物车操作后调一次,生成 3-5 个 follow-up chips。
"""

from __future__ import annotations

from typing import ClassVar

import structlog
from pydantic import BaseModel, ConfigDict, Field

from server import config
from server.tools.base import AgentDeps, Tool, ToolResult

log = structlog.get_logger("shopmind.tools.suggestions")


class Suggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(max_length=20, description="chip 显示文字,建议 <12 字")
    query: str = Field(description="用户点击 chip 后自动发送的 query 原文")


class ShowSuggestionsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[Suggestion] = Field(
        min_length=config.SUGGESTIONS_MIN_COUNT,
        max_length=config.SUGGESTIONS_MAX_COUNT,
        description=(
            f"{config.SUGGESTIONS_MIN_COUNT}-{config.SUGGESTIONS_MAX_COUNT} 个 follow-up 建议。"
            "必须继承 session_state.rejected_brands 和 mentioned_price_cap "
            "(不能 follow-up 用户拒绝过的品牌)。"
        ),
    )


class ShowSuggestionsTool(Tool):
    name: ClassVar[str] = "show_suggestions"
    description: ClassVar[str] = (
        "在主回复**之前**调,emit 3-5 个 follow-up chip 到客户端。"
        "推荐 / 对比 / 购物车操作 / 下单后调;闲聊 / no_match / 错误**不调**。"
        "suggestions 不能违背 session_state 已记录的约束(rejected_brands / price_cap)。"
    )
    input_model: ClassVar[type[BaseModel]] = ShowSuggestionsInput

    async def _run(
        self,
        *,
        user_id: str,
        deps: AgentDeps,
        validated_input: BaseModel,
    ) -> ToolResult:
        assert isinstance(validated_input, ShowSuggestionsInput)
        items = [s.model_dump() for s in validated_input.items]
        log.info("suggestions_emitted", user_id=user_id, n=len(items))
        return ToolResult(
            payload={"ok": True, "count": len(items)},
            suggestions=items,
        )


__all__ = ["ShowSuggestionsTool", "Suggestion"]
