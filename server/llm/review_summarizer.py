"""离线评论摘要抽取(Sonnet 4.6 + Tool Use,§4.5.4)。

一次调用产出**平衡摘要**:highlights(优点)+ caveats_text(注意点),
对标 Amazon "Customers say" / 京东 AI 评价总结 —— 两面都给,
注意点才显得客观可信,而非像在黑商品。

Prompt 设计原则:
- 中性客观陈述,不带情绪("部分用户反馈..." / "多数用户认可...")
- 每字段 1-2 句话,各 ≤ CAVEATS_TEXT_MAX_CHARS 字
- 某一面无成模式信号 → 该字段 null(两字段各自独立可空)
- 不准编造,只能基于评论实际内容
"""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel, Field

from server import config
from server.llm.anthropic_client import get_anthropic_client


class ReviewSummary(BaseModel):
    """Sonnet 输出契约:从评论提炼的平衡摘要。"""

    highlights: str | None = Field(
        default=None,
        description="客观优点要点,1-2 句话。评论中无成模式正面信号时返回 null。",
    )
    caveats_text: str | None = Field(
        default=None,
        description="客观警示文本,1-2 句话。评论中无明显负面信号时返回 null。",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="对该摘要的信心,基于评论一致性 / 样本量 / 信号强度。",
    )


_SYSTEM = f"""你是电商商品诚实推荐助手。读用户对某商品的多条评论,提炼一段**平衡摘要**,
供潜在买家快速了解真实口碑,分两面:
- highlights:用户**反复称赞**的优点(功效 / 体验 / 性价比 / 正品保障等)
- caveats_text:可能影响购买的**客观警示**(刺激性 / 不适人群 / 质量缺陷 / 性价比问题)

严格规则:
1. 两个字段都只能基于评论**实际提到的内容**,不准编造、不准从商品名 / 卖点反推
2. 中性客观陈述,不带主观情绪(用"部分用户反馈..."而非"很差";用"多数用户认可..."而非"超好用")
3. 每字段 1-2 句话,各 **总长度 ≤ {config.CAVEATS_TEXT_MAX_CHARS} 字**;合并同类信号,只挑最高频 / 最有信号的
4. 某一面**没有成模式的信号**(或信号零散),该字段返回 null;highlights 与 caveats_text 各自独立可空
5. 若给出总评论数 / 差评数,按差评真实占比校准 caveats 措辞("少数用户反馈..." / "较多用户反馈..."),
   但占比小**不代表可略** —— 安全 / 质量类问题仍须提示
6. confidence 反映你对整体摘要的信心(评论一致 + 样本足够 → 高)
必须通过 summarize_reviews 工具返回结构化结果。"""


_TOOL_SCHEMA: dict = {
    "name": "summarize_reviews",
    "description": "记录从评论中提炼的平衡摘要(优点 + 注意点)。",
    "input_schema": {
        "type": "object",
        "properties": {
            "highlights": {
                "type": ["string", "null"],
                "description": "1-2 句客观优点;无成模式正面信号则 null",
            },
            "caveats_text": {
                "type": ["string", "null"],
                "description": "1-2 句客观警示;无明显负面信号则 null",
            },
            "confidence": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "description": "对该摘要的信心",
            },
        },
        "required": ["highlights", "caveats_text", "confidence"],
    },
}


def _format_reviews(reviews: Iterable[tuple[int, str]]) -> str:
    """格式化为带 [rating星] 前缀的评论列表,便于 LLM 综合判断。"""
    lines: list[str] = []
    for idx, (rating, content) in enumerate(reviews, start=1):
        lines.append(f"{idx}. [{rating}星] {content}")
    return "\n".join(lines)


async def summarize_reviews(
    product_title: str,
    reviews: list[tuple[int, str]],
    *,
    total_reviews: int | None = None,
    negative_reviews: int | None = None,
) -> ReviewSummary:
    """基于 (rating, content) 列表抽平衡摘要。

    reviews 是调用方**分层采样**后的好评 + 差评(见 ingest §4.5.2)。
    total_reviews / negative_reviews 传真实总数,让模型据差评占比校准措辞
    (采样后样本里差评占比会被人为抬高,不能直接当真实占比)。
    传入空 reviews → 直接返回 (null, null, 0.0),不发 API。
    """
    if not reviews:
        return ReviewSummary(highlights=None, caveats_text=None, confidence=0.0)

    client = get_anthropic_client()
    context = ""
    if total_reviews is not None and negative_reviews is not None:
        context = (
            f"(真实总评论 {total_reviews} 条,其中差评 {negative_reviews} 条;"
            f"以下为分层采样,差评已保底纳入,勿按样本占比判断普遍性)\n"
        )
    user_msg = (
        f"商品: {product_title}\n\n"
        f"{context}"
        f"用户评论 ({len(reviews)} 条):\n{_format_reviews(reviews)}"
    )
    response = await client.messages.create(
        model=config.LLM_AGENT_MODEL,
        max_tokens=1024,
        system=_SYSTEM,
        tools=[_TOOL_SCHEMA],
        tool_choice={"type": "tool", "name": "summarize_reviews"},
        messages=[{"role": "user", "content": user_msg}],
    )
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "summarize_reviews":
            return ReviewSummary.model_validate(block.input)
    raise RuntimeError("Sonnet 未按 tool 协议返回 summarize_reviews")


__all__ = ["ReviewSummary", "summarize_reviews"]
