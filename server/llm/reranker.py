"""Reranker LLM 调用层(§4.3)。

只负责 "(query, 候选文本) → list[(product_id, score, reason)]" 这一步,
**不**做 DB enrich / 阈值过滤 / 排序,留给 `server/rag/reranking/llm_reranker.py` 编排。

调用契约:
- Haiku 4.5(`config.LLM_FAST_MODEL`),temperature=0,max_tokens=2048
- Tool Use `rank_products` 强制结构化输出
- system prompt + few-shot 走 Prompt Cache(80%+ 命中省钱)
- SDK 自带 max_retries=2,业务层不写循环
- API 异常 / Pydantic 校验失败 → 抛 `RerankerError`(上游兜底)
"""

from __future__ import annotations

import time
from typing import Any

import structlog
from anthropic import APIError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from server import config
from server.llm.anthropic_client import get_anthropic_client
from server.llm.prompts.reranker import (
    RERANKER_FEW_SHOT_MESSAGES,
    RERANKER_FEWSHOT_TOOL_USE_ID,
    RERANKER_SYSTEM_PROMPT,
)

log = structlog.get_logger("shopmind.reranker")


class RerankerError(RuntimeError):
    """Reranker Hard Fail — API / schema 校验异常,上游兜底。"""


# ──────────────────────────────────────────────────────────────────────
# LLM 输出契约(rank_products 工具)
# ──────────────────────────────────────────────────────────────────────
class RankerScoreItem(BaseModel):
    """单个候选的 LLM 评分(score + 简短 reason)。"""

    model_config = ConfigDict(extra="forbid")

    product_id: str
    relevance_score: float = Field(ge=0.0, le=1.0)
    reason: str | None = None


class RankerScores(BaseModel):
    """LLM 全部候选的评分集合。"""

    model_config = ConfigDict(extra="forbid")

    ranked: list[RankerScoreItem]


_TOOL_NAME = "rank_products"

_RANK_PRODUCTS_TOOL: dict[str, Any] = {
    "name": _TOOL_NAME,
    "description": "为每个候选商品打 0-1 的 relevance_score,必须通过本工具返回。",
    "input_schema": RankerScores.model_json_schema(),
}


def _build_system_blocks() -> list[dict[str, Any]]:
    """整段 system(规则 + few-shot 锚点)一起 cache,变化频率近零。"""
    return [
        {
            "type": "text",
            "text": RERANKER_SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _build_messages(formatted_candidates_text: str) -> list[dict[str, Any]]:
    """few-shot 末条是 assistant tool_use,真实 user 消息需前置 tool_result 块关闭。"""
    messages: list[dict[str, Any]] = list(RERANKER_FEW_SHOT_MESSAGES)
    messages.append(
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": RERANKER_FEWSHOT_TOOL_USE_ID,
                    "content": "ok",
                },
                {"type": "text", "text": formatted_candidates_text},
            ],
        }
    )
    return messages


def _extract_tool_input(content_blocks: list[Any]) -> dict[str, Any]:
    for block in content_blocks:
        if getattr(block, "type", None) == "tool_use" and block.name == _TOOL_NAME:
            return block.input  # type: ignore[no-any-return]
    raise RerankerError(f"Reranker 未按 tool 协议返回 {_TOOL_NAME}")


async def rank_candidates(formatted_text: str) -> RankerScores:
    """formatted_text → RankerScores(LLM 调用,异常透传 RerankerError)。

    formatted_text 应已经包含 `# Query: ...` 和 `# Candidates:` 列表
    (由上游 `LLMReranker` 拼好),本层不感知商品 schema。
    """
    if not formatted_text.strip():
        raise RerankerError("formatted_text 为空")

    client = get_anthropic_client()
    started = time.perf_counter()
    try:
        response = await client.messages.create(
            model=config.LLM_FAST_MODEL,
            max_tokens=2048,
            temperature=0.0,
            system=_build_system_blocks(),
            tools=[_RANK_PRODUCTS_TOOL],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            messages=_build_messages(formatted_text),
        )
    except APIError as e:
        log.error("reranker_api_failed", error=str(e))
        raise RerankerError(f"Reranker API 调用失败: {e}") from e

    try:
        tool_input = _extract_tool_input(response.content)
        scores = RankerScores.model_validate(tool_input)
    except ValidationError as e:
        log.error("reranker_invalid_output", error=str(e))
        raise RerankerError(f"Reranker 输出 schema 校验失败: {e}") from e

    duration_ms = int((time.perf_counter() - started) * 1000)
    log.info(
        "reranker_call",
        success=True,
        duration_ms=duration_ms,
        n_scored=len(scores.ranked),
    )
    return scores


__all__ = ["rank_candidates", "RankerScoreItem", "RankerScores", "RerankerError"]
