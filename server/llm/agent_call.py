"""Agent 主对话流式调用(§4.6.4 + §4.6.10)。

职责:
- 调用 Claude Sonnet 4.6,启用 Extended Thinking + tools
- streaming:把 SDK 的 `thinking_delta` / `text_delta` 转成上层 SSE event
- 返回最终 `Message`(orchestrator 据此判断 stop_reason / tool_uses)

不做:
- Tool loop(由 orchestrator 控制)
- 错误兜底(异常透传给 orchestrator)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import structlog
from anthropic import AsyncAnthropic
from anthropic.types import Message

from server import config
from server.llm.anthropic_client import get_anthropic_client
from server.llm.prompts.agent import AGENT_SYSTEM_PROMPT

log = structlog.get_logger("shopmind.agent_call")


@dataclass(slots=True)
class StreamChunk:
    """流式 chunk:type 决定 data 含义。

    type:
      - "thinking" → data["delta"] = str(思考增量)
      - "text"     → data["delta"] = str(回复增量)
      - "final"    → data["message"] = anthropic Message(用完即丢)
    """

    type: str
    data: dict[str, Any]


def _build_system_blocks(extra_context: str | None) -> list[dict[str, Any]]:
    """system 段拆两块:静态规则(cache)+ 动态上下文(profile / session_state)。"""
    blocks: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": AGENT_SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    if extra_context:
        blocks.append({"type": "text", "text": extra_context})
    return blocks


async def stream_agent_turn(
    *,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    extra_context: str | None = None,
    client: AsyncAnthropic | None = None,
) -> AsyncIterator[StreamChunk]:
    """单次 Claude 调用(可能包含工具调用 + 思考 + 文本),全部流式 yield。

    最终一个 chunk type="final" 携带完整 Message,orchestrator 拿它判断 stop_reason
    + 抽 tool_use。

    Extended Thinking quirk:thinking 启用时 Claude SDK 要求 temperature=1。
    """
    cli = client if client is not None else get_anthropic_client()
    thinking_enabled = config.THINKING_BUDGET_TOKENS > 0

    kwargs: dict[str, Any] = {
        "model": config.LLM_AGENT_MODEL,
        "max_tokens": config.AGENT_MAX_TOKENS,
        "system": _build_system_blocks(extra_context),
        "tools": tools,
        "tool_choice": {"type": "auto"},
        "messages": messages,
    }
    if thinking_enabled:
        kwargs["thinking"] = {
            "type": "enabled",
            "budget_tokens": config.THINKING_BUDGET_TOKENS,
        }
        kwargs["temperature"] = 1.0  # SDK 硬约束
    else:
        kwargs["temperature"] = 0.3

    async with cli.messages.stream(**kwargs) as stream:
        async for event in stream:
            # Anthropic SDK 的事件类型在 4.x 中是 `RawMessageStreamEvent` 的子类
            event_type = getattr(event, "type", None)
            if event_type != "content_block_delta":
                continue
            delta = getattr(event, "delta", None)
            if delta is None:
                continue
            delta_type = getattr(delta, "type", None)
            if delta_type == "thinking_delta":
                text = getattr(delta, "thinking", "") or ""
                if text:
                    yield StreamChunk("thinking", {"delta": text})
            elif delta_type == "text_delta":
                text = getattr(delta, "text", "") or ""
                if text:
                    yield StreamChunk("text", {"delta": text})

        final: Message = await stream.get_final_message()

    log.info(
        "agent_turn_done",
        stop_reason=final.stop_reason,
        n_blocks=len(final.content),
        input_tokens=final.usage.input_tokens,
        output_tokens=final.usage.output_tokens,
    )
    yield StreamChunk("final", {"message": final})


__all__ = ["StreamChunk", "stream_agent_turn"]
