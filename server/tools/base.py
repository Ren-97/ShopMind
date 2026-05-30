"""Tools 注册 + 执行底座(§4.6.1 + §4.6.8 + §4.6.9)。

设计要点:
- 每个 Tool 子类声明 `name` / `description` / `input_model`(Pydantic v2),
  Anthropic input_schema 直接从 `model_json_schema()` 派生 — schema 单一真相源
- `execute_tool` 注入 `user_id` + `AgentDeps`,**Claude 看到的 schema 不含 user_id**
- Tool 异常 → 包成 `{"error": "..."}` 回灌 Claude(§4.6.9 第 1 层防御)
- Tool 输出 `ToolResult`:`payload` 给 LLM,`cards` / `suggestions` 是
  SSE 副作用,由 orchestrator 转 SSE event(§4.6.4 / §4.7.1)
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

import structlog
from anthropic.types import ToolUseBlock
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from server.rag.reranking.protocol import Reranker
from server.rag.retrieval.dispatcher import RetrievalDispatcher

log = structlog.get_logger("shopmind.tools")


# ──────────────────────────────────────────────────────────────────────
# 共享依赖容器 — orchestrator 启动时构造一次,所有 tool 共享
# ──────────────────────────────────────────────────────────────────────
@dataclass(slots=True)
class AgentDeps:
    """跨 tool 共享的可注入依赖(不含 user_id — user_id 是 per-turn 注入)。"""

    session_factory: async_sessionmaker[AsyncSession]
    dispatcher: RetrievalDispatcher
    reranker: Reranker
    base_url: str  # 拼 image_url 用


# ──────────────────────────────────────────────────────────────────────
# Tool 输出契约
# ──────────────────────────────────────────────────────────────────────
class ToolResult(BaseModel):
    """单次 tool 调用的全部输出。

    - `payload`:json-serializable,作为 tool_result 内容回灌给 Claude
    - `cards`:SSE `event: card` 列表(orchestrator 转发,§4.7 lean schema)
    - `suggestions`:SSE `event: suggestions` items(由 show_suggestions 产生)
    - `meta`:**不进 Claude payload**,只给 orchestrator 用(例如 search 把
      Planner 的 plan 回传给 session_state 更新)
    - `is_error`:True 时 Claude 收到 tool_result 会标 is_error,用于 Agent
      "换个工具救场"的判断
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    payload: dict[str, Any] = Field(default_factory=dict)
    cards: list[dict[str, Any]] = Field(default_factory=list)
    suggestions: list[dict[str, Any]] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
    is_error: bool = False


class ToolError(RuntimeError):
    """tool 内部业务错误(参数校验失败 / DB 未命中等)。
    `execute_tool` 包成 `{"error": ...}` 回灌 Claude,不外抛。
    """


# ──────────────────────────────────────────────────────────────────────
# Tool 基类
# ──────────────────────────────────────────────────────────────────────
class Tool(ABC):
    """所有 7 个 tool 的统一基类。

    子类只需实现 `_run(user_id, deps, **validated_input)` 即可,base 负责:
      - Pydantic 校验 input(防 Claude 乱传 / 缺字段)
      - 异常包装(ToolError → is_error=True;其它异常上抛)
      - 日志
    """

    name: ClassVar[str]
    description: ClassVar[str]
    input_model: ClassVar[type[BaseModel]]

    @classmethod
    def anthropic_schema(cls) -> dict[str, Any]:
        """Anthropic Tool 定义 — `input_schema` 直接来自 Pydantic JSON Schema。"""
        return {
            "name": cls.name,
            "description": cls.description,
            "input_schema": cls.input_model.model_json_schema(),
        }

    @abstractmethod
    async def _run(
        self,
        *,
        user_id: str,
        deps: AgentDeps,
        validated_input: BaseModel,
    ) -> ToolResult: ...

    async def run(
        self,
        *,
        user_id: str,
        deps: AgentDeps,
        raw_input: dict[str, Any],
    ) -> ToolResult:
        try:
            validated = self.input_model.model_validate(raw_input)
        except ValidationError as e:
            log.warning(
                "tool_input_invalid", tool=self.name, error=str(e), raw=raw_input
            )
            return ToolResult(
                payload={"error": f"参数校验失败: {e.errors()[:3]}"},
                is_error=True,
            )

        try:
            return await self._run(
                user_id=user_id, deps=deps, validated_input=validated
            )
        except ToolError as e:
            log.info("tool_business_error", tool=self.name, error=str(e))
            return ToolResult(payload={"error": str(e)}, is_error=True)


# ──────────────────────────────────────────────────────────────────────
# Registry — 由 build_tool_registry() 装配(避免循环 import)
# ──────────────────────────────────────────────────────────────────────
def build_tool_registry() -> dict[str, Tool]:
    """构造 `{tool_name: Tool instance}`,在 orchestrator 启动时调一次。

    放在函数里是为了让 import server.tools 不立即触发各 tool 模块的副作用
    (例如 search 间接 import dispatcher / reranker)。
    """
    from server.tools.cart import ManageCartTool
    from server.tools.compare import CompareProductsTool
    from server.tools.order import PlaceOrderTool
    from server.tools.preference import UpdatePreferenceTool
    from server.tools.recall import RecallHistoryTool
    from server.tools.search import SearchProductsTool
    from server.tools.suggestions import ShowSuggestionsTool

    tools: list[Tool] = [
        SearchProductsTool(),
        CompareProductsTool(),
        ManageCartTool(),
        PlaceOrderTool(),
        UpdatePreferenceTool(),
        RecallHistoryTool(),
        ShowSuggestionsTool(),
    ]
    return {t.name: t for t in tools}


def tool_specs_for_anthropic(registry: dict[str, Tool]) -> list[dict[str, Any]]:
    """把 registry 转成 Anthropic API 接受的 tools 列表。"""
    return [type(t).anthropic_schema() for t in registry.values()]


# ──────────────────────────────────────────────────────────────────────
# execute_tool — orchestrator 的统一入口(§4.6.9 异常包装)
# ──────────────────────────────────────────────────────────────────────
async def execute_tool(
    tool_use: ToolUseBlock | Any,
    *,
    user_id: str,
    deps: AgentDeps,
    registry: dict[str, Tool],
) -> ToolResult:
    """执行单个 tool call。任何未捕获异常包成 `{"error": ...}`。

    与 §4.6.9 第 1 层防御对齐:单 tool 失败不崩 turn,Claude 还能换 tool 救场。
    """
    name = getattr(tool_use, "name", None) or "<unknown>"
    raw_input = getattr(tool_use, "input", {}) or {}

    tool = registry.get(name)
    if tool is None:
        log.warning("tool_unknown", name=name)
        return ToolResult(
            payload={"error": f"未知工具 {name}"},
            is_error=True,
        )

    try:
        return await tool.run(user_id=user_id, deps=deps, raw_input=raw_input)
    except Exception as e:  # noqa: BLE001 — 第 1 层防御:吞所有未预期异常
        log.error(
            "tool_execution_failed",
            tool=name,
            error=str(e),
            exc_info=True,
        )
        return ToolResult(
            payload={"error": f"{name} 调用失败: {str(e)[:200]}"},
            is_error=True,
        )


def serialize_payload_for_claude(payload: dict[str, Any]) -> str:
    """把 tool_result payload 转 JSON 字符串(Anthropic tool_result.content 接受 str)。"""
    return json.dumps(payload, ensure_ascii=False, default=str)


__all__ = [
    "AgentDeps",
    "Tool",
    "ToolError",
    "ToolResult",
    "build_tool_registry",
    "execute_tool",
    "serialize_payload_for_claude",
    "tool_specs_for_anthropic",
]
