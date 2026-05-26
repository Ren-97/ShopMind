"""离线 Caveats 抽取(Sonnet 4.6 + Tool Use,§4.5.4)。

Prompt 设计原则:
- 只提负面 / 警示信号(刺激性、不适人群、质量、性价比)
- 中性客观陈述,不带情绪
- 1-2 句话,≤ CAVEATS_TEXT_MAX_CHARS 字
- 无明显负面 → caveats_text=null
- 不准编造,只能基于评论实际内容
"""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel, Field

from server import config
from server.llm.anthropic_client import get_anthropic_client


class CaveatsResult(BaseModel):
    """Sonnet 输出契约。"""

    caveats_text: str | None = Field(
        default=None,
        description="客观警示文本,1-2 句话 ≤ 200 字。评论中无明显负面信号时返回 null。",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="对该 caveats 的信心,基于评论一致性 / 样本量 / 信号强度。",
    )


_SYSTEM = f"""你是电商商品诚实推荐助手。读用户对某商品的多条评论,只做一件事:
**抽出可能影响潜在买家的客观警示信号**(刺激性 / 不适人群 / 质量缺陷 / 性价比问题)。

严格规则:
1. 只能基于评论**实际提到的内容**,不准编造、不准从商品名 / 卖点反推
2. 中性客观陈述,不带主观情绪(用"部分用户反馈..."而非"很差")
3. 1-2 句话,**总长度 ≤ {config.CAVEATS_TEXT_MAX_CHARS} 字**
4. 多个不同负面信号合并到一句话里;只挑最高频 / 最有信号的 1-2 条
5. 如果评论中**没有明确负面信号**(或负面信号零散不成模式),返回 caveats_text=null
6. confidence 反映你对该结论的信心(评论一致 + 样本足够 → 高)
必须通过 extract_caveats 工具返回结构化结果。"""


_TOOL_SCHEMA: dict = {
    "name": "extract_caveats",
    "description": "记录从评论中提炼的客观警示信号。",
    "input_schema": {
        "type": "object",
        "properties": {
            "caveats_text": {
                "type": ["string", "null"],
                "description": "1-2 句客观警示;无明显负面信号则 null",
            },
            "confidence": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "description": "对该 caveats 的信心",
            },
        },
        "required": ["caveats_text", "confidence"],
    },
}


def _format_reviews(reviews: Iterable[tuple[int, str]]) -> str:
    """格式化为带 [rating星] 前缀的评论列表,便于 LLM 综合判断。"""
    lines: list[str] = []
    for idx, (rating, content) in enumerate(reviews, start=1):
        lines.append(f"{idx}. [{rating}星] {content}")
    return "\n".join(lines)


async def extract_caveats(
    product_title: str, reviews: list[tuple[int, str]]
) -> CaveatsResult:
    """基于 (rating, content) 列表抽 caveats。

    评论应为**规则过滤后的 quality reviews**(由调用方过滤,见 §4.5.2 Step 1)。
    传入空 reviews → 直接返回 (null, 0.0),不发 API。
    """
    if not reviews:
        return CaveatsResult(caveats_text=None, confidence=0.0)

    client = get_anthropic_client()
    user_msg = (
        f"商品: {product_title}\n\n"
        f"用户评论 ({len(reviews)} 条):\n{_format_reviews(reviews)}"
    )
    response = await client.messages.create(
        model=config.LLM_AGENT_MODEL,
        max_tokens=1024,
        system=_SYSTEM,
        tools=[_TOOL_SCHEMA],
        tool_choice={"type": "tool", "name": "extract_caveats"},
        messages=[{"role": "user", "content": user_msg}],
    )
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "extract_caveats":
            return CaveatsResult.model_validate(block.input)
    raise RuntimeError("Sonnet 未按 tool 协议返回 extract_caveats")


__all__ = ["CaveatsResult", "extract_caveats"]
