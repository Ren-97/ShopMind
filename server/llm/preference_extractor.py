"""偏好抽取器(记忆抽取层,§4.4)。

输入用户单轮原话 → 输出结构化排除/接受信号(Haiku 4.5 + 强制 Tool Use)。

为什么独立成层(而不是塞给聊天 Agent 的 update_preference):
- **每轮必跑、强制结构化输出**:不靠聊天模型自己决定要不要记(它会漏 / 假称"已记下")。
- **理解归 LLM**:任意说法都能懂("不穿/接受不了/那牌子别推了"),不打关键词地鼠。
- 抽完由 orchestrator 用代码落档 + 过滤(执行归代码)。

调用契约同 Planner:Haiku、temperature=0、system+few-shot 走 Prompt Cache、
SDK 自带 max_retries。**失败不抛**:抽取是增强、不该阻断主流程,异常 → 返回空信号。
"""

from __future__ import annotations

import time
from typing import Any

import structlog
from anthropic import APIError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from server import config
from server.domain.types import Origin
from server.llm.anthropic_client import get_anthropic_client
from server.llm.prompts.preference_extractor import (
    PREF_EXTRACTOR_FEW_SHOT,
    PREF_EXTRACTOR_SYSTEM_PROMPT,
)

log = structlog.get_logger("shopmind.pref_extractor")

_TOOL_NAME = "record_exclusions"


class ExtractedExclusions(BaseModel):
    """抽取器输出。空 = 这句话没有品牌/产地排除信号。

    分**长期**(写 profile 永久 + 会话态)和**本次**(只进会话态,不污染永久档案):
    "我不买 X / 从来不用 X" → 长期;"这次/今天不要 X" 或语气不确定 → 本次。
    """

    model_config = ConfigDict(extra="forbid")

    # 长期稳定 → 写 profile + 会话态
    brand_exclude: list[str] = Field(default_factory=list)
    origin_exclude: list[Origin] = Field(default_factory=list)
    # 仅本次 / 不确定 → 只进会话态(本场有效,不写永久档案)
    brand_exclude_session: list[str] = Field(default_factory=list)
    origin_exclude_session: list[Origin] = Field(default_factory=list)
    # 改口接受 → 从 profile + 会话态移除
    brand_unexclude: list[str] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not (
            self.brand_exclude
            or self.origin_exclude
            or self.brand_exclude_session
            or self.origin_exclude_session
            or self.brand_unexclude
        )


_RECORD_TOOL: dict[str, Any] = {
    "name": _TOOL_NAME,
    "description": "返回用户这句话里的品牌/产地排除(及改口接受)信号。必须通过本工具返回。",
    "input_schema": ExtractedExclusions.model_json_schema(),
}


def _build_system_blocks() -> list[dict[str, Any]]:
    return [
        {
            "type": "text",
            "text": PREF_EXTRACTOR_SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _build_messages(user_message: str) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = list(PREF_EXTRACTOR_FEW_SHOT)
    # few-shot 末条是 assistant(tool_use),真实 user 前置 tool_result 关闭协议
    messages.append(
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_fewshot_prefextract",
                    "content": "ok",
                },
                {"type": "text", "text": user_message},
            ],
        }
    )
    return messages


def _extract_tool_input(content_blocks: list[Any]) -> dict[str, Any] | None:
    for block in content_blocks:
        if getattr(block, "type", None) == "tool_use" and block.name == _TOOL_NAME:
            return block.input  # type: ignore[no-any-return]
    return None


# 成本闸门(只决定"要不要花这次 LLM 调用",**不做理解**):没有任何否定/厌恶/改口标记的
# 句子("谢谢""第二个""加购物车""我要 Nike")不可能是排除/接受,直接跳过、省一次 Haiku。
# 理解仍全交给 LLM —— 闸门误放行顶多浪费一次调用(LLM 返回空),无害;**只要不漏放行即可**。
# 注:这是"尽量铺全"的人工清单,不是穷举(开放式自然语言列不全);极偏说法可能漏,是
# 刻意取舍(省去维护品牌词表)。主力是单字"不/别",已覆盖绝大多数(不买/不穿/不用/不要/
# 接受不了/信不过/别推/别给…);其余补的是不含"不/别"的厌恶/改口说法。
_SIGNAL_MARKERS: tuple[str, ...] = (
    # 否定主力(单字,覆盖面最广)
    "不", "别",
    # 厌恶 / 排斥(不含"不/别"的)
    "讨厌", "烦", "厌", "腻", "拒绝", "排除", "排斥", "避开", "避雷", "踩雷",
    "拉黑", "无感", "反感", "没好感", "没兴趣", "看不上", "免了", "划掉",
    "跳过", "去掉", "移除", "pass", "除了",
    # 改口接受(从黑名单移除)
    "算了", "其实", "也行", "也可以", "可以了", "能接受", "换成", "改成",
)


def _might_express_preference(text: str) -> bool:
    return any(m in text for m in _SIGNAL_MARKERS)


async def extract_exclusions(user_message: str) -> ExtractedExclusions:
    """user_message → ExtractedExclusions。失败/空输入/无信号 → 空(绝不抛,不阻断主流程)。"""
    if not user_message or not user_message.strip():
        return ExtractedExclusions()
    if not _might_express_preference(user_message):
        return ExtractedExclusions()

    client = get_anthropic_client()
    started = time.perf_counter()
    try:
        response = await client.messages.create(
            model=config.LLM_FAST_MODEL,
            max_tokens=256,
            temperature=0.0,
            system=_build_system_blocks(),
            tools=[_RECORD_TOOL],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            messages=_build_messages(user_message),
        )
        tool_input = _extract_tool_input(response.content)
        if tool_input is None:
            return ExtractedExclusions()
        result = ExtractedExclusions.model_validate(tool_input)
    except (APIError, ValidationError) as e:
        # 抽取是增强能力,失败不该让用户这轮挂掉 —— 退化成"这轮没抽到"
        log.warning("pref_extractor_failed", error=str(e))
        return ExtractedExclusions()

    if not result.is_empty():
        log.info(
            "pref_extractor_hit",
            duration_ms=int((time.perf_counter() - started) * 1000),
            brand_exclude=result.brand_exclude,
            origin_exclude=result.origin_exclude,
            brand_unexclude=result.brand_unexclude,
        )
    return result


__all__ = ["extract_exclusions", "ExtractedExclusions"]
