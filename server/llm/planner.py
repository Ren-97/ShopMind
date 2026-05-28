"""Query Planner(§4.2)。

输入:user_query + 可选上下文(profile / session_state / recent_turns)
输出:`QueryPlan` 严格 Pydantic 模型(extra='forbid')

调用契约:
- 走 Haiku 4.5(`config.LLM_FAST_MODEL`),`temperature=0`,`max_tokens=512`
- Tool Use 强制 `make_query_plan`,LLM 不能自由文本回复
- system prompt + few-shot 标 `cache_control: ephemeral` → Anthropic Prompt Cache(§4.8.1)
- SDK 自带 `max_retries=2`,业务层不写重试循环
- 异常:`anthropic.APIError` / `ValidationError` / 工具调用缺失 → 抛 `PlannerError`
       (上游 Agent 层转 Hard Fail 兜底文案,**不构造降级 plan**,§4.2.7)

低置信(plan.confidence < 0.7)由 Dispatcher 在退化层处理(§4.1.4),
本模块不预判 — 我们只保证返回一个合法 plan 或抛错。
"""

from __future__ import annotations

import time
from typing import Any

import structlog
from anthropic import APIError
from pydantic import ValidationError

from server import config
from server.domain.types import QueryPlan
from server.llm.anthropic_client import get_anthropic_client
from server.llm.prompts.planner import (
    PLANNER_FEW_SHOT_MESSAGES,
    PLANNER_SYSTEM_PROMPT,
)

log = structlog.get_logger("shopmind.planner")


class PlannerError(RuntimeError):
    """Planner Hard Fail — API / 校验异常,上游兜底文案。"""


_TOOL_NAME = "make_query_plan"

# Tool input_schema 直接来自 Pydantic QueryPlan,schema 单一真相源(§4.2.4)。
# Anthropic Tool API 接受 JSON Schema draft 2020-12,Pydantic 默认输出兼容。
_QUERY_PLAN_TOOL: dict[str, Any] = {
    "name": _TOOL_NAME,
    "description": "把用户 query + 多轮上下文 转成结构化检索 plan。必须通过本工具返回。",
    "input_schema": QueryPlan.model_json_schema(),
}


def _render_context(
    *,
    profile: dict[str, Any] | None,
    session_state: dict[str, Any] | None,
) -> str:
    """把 profile + session_state 拼成 system prompt 的第二个 cache 段。

    分两段是为了让静态规则段独立 cache(变化频率极低,几乎永远命中),
    而 profile / session_state 是 per-user / per-session 的(不参与 cache)。
    """
    sections: list[str] = []
    if profile:
        sections.append(f"[用户档案] {profile}")
    if session_state:
        sections.append(f"[本轮 session 已沉淀] {session_state}")
    if not sections:
        return "[无额外上下文]"
    return "\n".join(sections)


def _build_system_blocks(context_text: str) -> list[dict[str, Any]]:
    """system 段拆两块:
    1) 规则 + few-shot 索引描述,cache 命中
    2) 动态 context(profile / session),不 cache
    """
    return [
        {
            "type": "text",
            "text": PLANNER_SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": context_text,
        },
    ]


# Few-shot 最后一条是 assistant(tool_use),按 Anthropic 协议下一条 user 必须
# 携带相同 id 的 tool_result。这里把 "关闭 few-shot 的 tool_result" 塞进真实
# user 流的第一条,既满足协议又不污染调用方 API。
_FEWSHOT_CLOSING_TOOL_USE_ID = "toolu_fewshot_planner"


def _close_fewshot_block() -> dict[str, Any]:
    return {
        "type": "tool_result",
        "tool_use_id": _FEWSHOT_CLOSING_TOOL_USE_ID,
        "content": "ok",
    }


def _build_messages(
    *,
    user_query: str,
    recent_turns: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """完整 messages 列表:few-shot → (tool_result + recent_turns + 当前 query)。

    recent_turns 由调用方按 Anthropic 协议格式好(role + content),
    Planner 不负责从 chat_history 表查 — 解耦,便于测试。

    协议处理:把 few-shot 末尾未关闭的 tool_result 塞进**紧接着的第一条 user 消息**
    作为首块,把 user 原文本作为第二块。这样无论是否有 recent_turns 都协议合法。
    """
    messages: list[dict[str, Any]] = list(PLANNER_FEW_SHOT_MESSAGES)

    closing = _close_fewshot_block()

    # 找到 "下一条要发的 user 消息":有 recent_turns 时是它的第一条 user,
    # 否则就是 user_query 本身
    if recent_turns:
        # 拷贝并改第一条 user,前置 tool_result 块
        injected_turns: list[dict[str, Any]] = []
        injected_closing = False
        for turn in recent_turns:
            if not injected_closing and turn.get("role") == "user":
                original_content = turn["content"]
                if isinstance(original_content, str):
                    new_content: list[dict[str, Any]] = [
                        closing,
                        {"type": "text", "text": original_content},
                    ]
                else:
                    new_content = [closing, *original_content]
                injected_turns.append({"role": "user", "content": new_content})
                injected_closing = True
            else:
                injected_turns.append(turn)
        messages.extend(injected_turns)
        if not injected_closing:
            # recent_turns 没有 user 消息(异常),直接把 closing 拼到当前 query
            messages.append(
                {
                    "role": "user",
                    "content": [closing, {"type": "text", "text": user_query}],
                }
            )
        else:
            messages.append({"role": "user", "content": user_query})
    else:
        messages.append(
            {
                "role": "user",
                "content": [closing, {"type": "text", "text": user_query}],
            }
        )
    return messages


def _extract_tool_input(content_blocks: list[Any]) -> dict[str, Any]:
    """从 response.content 拿 make_query_plan 的 input。"""
    for block in content_blocks:
        if getattr(block, "type", None) == "tool_use" and block.name == _TOOL_NAME:
            return block.input  # type: ignore[no-any-return]
    raise PlannerError(f"Planner 未按 tool 协议返回 {_TOOL_NAME}")


async def plan_query(
    user_query: str,
    *,
    profile: dict[str, Any] | None = None,
    session_state: dict[str, Any] | None = None,
    recent_turns: list[dict[str, Any]] | None = None,
) -> QueryPlan:
    """user_query → QueryPlan(Haiku 4.5 + Tool Use)。

    抛 `PlannerError`:Anthropic API 错误 / Pydantic 校验失败 / 工具调用缺失。
    """
    if not user_query or not user_query.strip():
        raise PlannerError("user_query 为空")

    client = get_anthropic_client()
    context_text = _render_context(profile=profile, session_state=session_state)
    system_blocks = _build_system_blocks(context_text)
    messages = _build_messages(user_query=user_query, recent_turns=recent_turns)

    started = time.perf_counter()
    try:
        response = await client.messages.create(
            model=config.LLM_FAST_MODEL,
            max_tokens=512,
            temperature=0.0,
            system=system_blocks,
            tools=[_QUERY_PLAN_TOOL],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            messages=messages,
        )
    except APIError as e:
        log.error("planner_api_failed", error=str(e))
        raise PlannerError(f"Planner API 调用失败: {e}") from e

    try:
        tool_input = _extract_tool_input(response.content)
        plan = QueryPlan.model_validate(tool_input)
    except (ValidationError, PlannerError) as e:
        log.error(
            "planner_invalid_output",
            error=str(e),
            raw=getattr(response, "model_dump", lambda: None)(),
        )
        if isinstance(e, PlannerError):
            raise
        raise PlannerError(f"Planner 输出 schema 校验失败: {e}") from e

    duration_ms = int((time.perf_counter() - started) * 1000)
    log.info(
        "planner_call",
        success=True,
        duration_ms=duration_ms,
        query_type=plan.query_type,
        confidence=plan.confidence,
        has_text_query=plan.text_query is not None,
    )
    return plan


__all__ = ["plan_query", "PlannerError"]
