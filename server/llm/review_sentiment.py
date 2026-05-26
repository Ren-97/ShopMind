"""每条 review 走一次 Haiku 4.5 抽 sentiment + aspects(§4.5.2 Step 2)。

设计:
- 基于**文本内容**判 sentiment,不依赖 rating(rating 噪声大,有时与正文相反)
- aspects 抽商品维度短语(如"敏感肌"、"保湿"、"性价比"),供检索 / 排序使用
- Tool Use 强制结构化输出,避免 LLM 自由说话飘走
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from server import config
from server.llm.anthropic_client import get_anthropic_client


class ReviewSentimentResult(BaseModel):
    """Haiku 输出契约 — 同时也是 Anthropic Tool Use 的 input_schema 来源。"""

    sentiment: float = Field(
        ...,
        ge=-1.0,
        le=1.0,
        description="基于评论内容判断的情感倾向。-1.0 强负面 / 0.0 中性 / +1.0 强正面。",
    )
    aspects: list[str] = Field(
        default_factory=list,
        description='评论提到的商品维度短语,如 ["敏感肌", "保湿", "性价比"]。无明显维度则空列表。',
    )


_SYSTEM = """你是电商评论分析助手。给一条用户评论,只判断两件事:
1. sentiment:基于评论**文字内容**给出 -1.0 到 +1.0 的情感分(不要看星级)。
2. aspects:抽 1-5 个评论提到的商品维度短语(中文,2-6 字),只保留客观维度词,不要重复。

必须通过 record_sentiment 工具返回结构化结果,不要自由说话。"""


_TOOL_SCHEMA: dict = {
    "name": "record_sentiment",
    "description": "记录一条评论的情感分与提到的商品维度。",
    "input_schema": {
        "type": "object",
        "properties": {
            "sentiment": {
                "type": "number",
                "minimum": -1.0,
                "maximum": 1.0,
                "description": "情感分,-1 强负面到 +1 强正面",
            },
            "aspects": {
                "type": "array",
                "items": {"type": "string"},
                "description": "评论提到的商品维度短语",
            },
        },
        "required": ["sentiment", "aspects"],
    },
}


async def classify_review(content: str) -> ReviewSentimentResult:
    """单条评论 → sentiment + aspects。

    抛错(API 失败 / JSON 解析失败)交给上层 ingest 主流程的 try/except 兜底,
    单商品 fail 跳过(§4.5.6),不阻塞批量。
    """
    client = get_anthropic_client()
    response = await client.messages.create(
        model=config.LLM_FAST_MODEL,
        max_tokens=512,
        system=_SYSTEM,
        tools=[_TOOL_SCHEMA],
        tool_choice={"type": "tool", "name": "record_sentiment"},
        messages=[{"role": "user", "content": content}],
    )
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "record_sentiment":
            return ReviewSentimentResult.model_validate(block.input)
    raise RuntimeError("Haiku 未按 tool 协议返回 record_sentiment")


__all__ = ["ReviewSentimentResult", "classify_review"]
