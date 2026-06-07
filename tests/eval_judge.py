"""Eval L3 — LLM-as-Judge,豆包(Doubao)via 火山引擎 Ark(OpenAI 兼容)。

为什么用豆包判 Claude(跨模型 judging):
- 同模型自评有 Self-Enhancement Bias —— 模型偏爱自己风格的输出(Zheng et al. 2023,
  "Judging LLM-as-a-Judge")。换一家模型判,结论更可信,也是答辩 talking point。

只判两个"必须语义理解、规则断言够不着"的维度(由 case 的 judge_focus 决定):
- `faithfulness`:回复里的商品事实(价格/规格/属性/库存)是否都能在"提供给 judge 的
  商品资料(cards)"里找到依据,有没有编造。对应防幻觉铁律 3。
- `source_attribution`:回复引用用户评论类信息时,有没有标注来源("据用户评论 / 有用户
  反馈"),没伪装成商家声明。对应事实分层 L3/L4。

设计:
- `build_judge_prompt` / `parse_judge_response` 是**纯函数**(无网络),offline 可单测。
- `judge_case` 是唯一打网络的入口;**没配 ARK_API_KEY → 整条 L3 优雅 skip**(返回 skipped
  verdict),不烧 key、不阻塞 L1/L2。
- 判分用 0/1(passed)+ reason,不做 1-5 打分 —— 防幻觉是"有/无"问题,二元更可复现。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import structlog

from server import config

log = structlog.get_logger("shopmind.eval.judge")

# 支持的判分维度(case.judge_focus 里出现才会判)
_KNOWN_DIMENSIONS = ("faithfulness", "source_attribution")

_DIMENSION_RUBRIC = {
    "faithfulness": (
        "faithfulness(忠实度/防幻觉):回复里出现的每一条商品事实(价格、规格、容量、"
        "成分、库存、品牌)都必须能在【商品资料】里找到依据。只要有一条事实在资料里查无"
        "出处、或与资料矛盾、或资料根本没有该商品却仍推荐了具体商品 → 判 fail。"
        "诚实地说'没找到/暂时没有/信息不足'不算 fail。"
    ),
    "source_attribution": (
        "source_attribution(来源标注):若回复引用了用户评论 / 主观体验类信息(如'用户反映"
        "保湿好''有人说偏小'),必须带'根据用户评论 / 有用户反馈 / 用户提到'之类的标注,不能"
        "把评论伪装成商家客观声明。若回复没引用任何评论类信息 → 该维度判 pass(N/A)。"
    ),
}


@dataclass(slots=True)
class JudgeVerdict:
    dimension: str
    passed: bool
    reason: str
    skipped: bool = False


# ──────────────────────────────────────────────────────────────────────
# 纯函数:prompt 构造 + 响应解析(offline 可单测)
# ──────────────────────────────────────────────────────────────────────
def requested_dimensions(case: dict) -> list[str]:
    """从 case.judge_focus 取出本 case 要判的维度(过滤掉不认识的)。"""
    focus = case.get("judge_focus") or []
    if isinstance(focus, str):
        focus = [focus]
    return [d for d in focus if d in _KNOWN_DIMENSIONS]


def build_judge_prompt(
    *,
    query: str,
    assistant_text: str,
    product_facts: list[dict[str, Any]],
    dimensions: list[str],
) -> str:
    """拼 judge 的 user prompt(纯文本)。

    product_facts = Agent 本轮从 tool 实际拿到的 ProductSummary(含 skus/容量、
    properties/成分、matched_chunks/评论原文)。这是判幻觉的唯一真相源:防幻觉铁律 3
    要求 Claude 只能引用 ProductSummary,所以"忠实于 product_facts" ≡ "无幻觉"。
    不喂 dataset 全集(Agent 没见过的不该拿来要求它),也不喂 lean card(字段不够判)。"""
    rubric = "\n".join(f"- {_DIMENSION_RUBRIC[d]}" for d in dimensions)
    facts_json = (
        json.dumps(product_facts, ensure_ascii=False, indent=2)
        if product_facts
        else "(本轮没有检索到任何商品 —— 若回复仍声称某具体商品存在,即为幻觉)"
    )
    return f"""你是电商导购回复的严格质检员。下面给你一轮对话:用户问题、AI 导购的回复、以及"AI 当时从系统检索到的商品资料"。请**只依据商品资料**判断回复在以下维度是否达标。

# 判分维度
{rubric}

# 用户问题
{query}

# AI 导购回复
{assistant_text or "(回复为空)"}

# 商品资料(judge 唯一可信来源,资料里没有的就是"无依据")
{facts_json}

# 输出
严格返回 JSON,不要任何解释文字,格式:
{{"verdicts": [{{"dimension": "<维度名>", "pass": true/false, "reason": "<30字内中文理由>"}}]}}
每个维度一条,dimension 必须取自:{dimensions}。"""


def parse_judge_response(raw: str, dimensions: list[str]) -> list[JudgeVerdict]:
    """解析 judge 返回的 JSON。容错:抽取第一个 {...} 段;缺失维度记为 fail(判官没回 = 不达标)。"""
    verdicts: list[JudgeVerdict] = []
    parsed: dict[str, Any] | None = None
    try:
        start = raw.index("{")
        end = raw.rindex("}") + 1
        parsed = json.loads(raw[start:end])
    except (ValueError, json.JSONDecodeError):
        parsed = None

    by_dim: dict[str, dict] = {}
    if parsed and isinstance(parsed.get("verdicts"), list):
        for v in parsed["verdicts"]:
            if isinstance(v, dict) and v.get("dimension") in dimensions:
                by_dim[v["dimension"]] = v

    for d in dimensions:
        v = by_dim.get(d)
        if v is None:
            verdicts.append(JudgeVerdict(d, False, "judge 未返回该维度判定", skipped=False))
            continue
        passed = bool(v.get("pass"))
        reason = str(v.get("reason", ""))[:60]
        verdicts.append(JudgeVerdict(d, passed, reason))
    return verdicts


# ──────────────────────────────────────────────────────────────────────
# 网络入口(唯一烧 key 处)
# ──────────────────────────────────────────────────────────────────────
_client: Any = None


def _get_ark_client() -> Any:
    """懒加载 AsyncOpenAI 指向火山 Ark。首次调用校验 ARK_API_KEY。"""
    global _client
    if _client is None:
        from openai import AsyncOpenAI

        _client = AsyncOpenAI(
            api_key=config.ARK_API_KEY,
            base_url=config.ARK_BASE_URL,
        )
    return _client


def judge_enabled() -> bool:
    """配了 ARK_API_KEY 才跑 L3。"""
    return bool(config.ARK_API_KEY)


async def judge_case(
    case: dict,
    *,
    assistant_text: str,
    product_facts: list[dict[str, Any]],
) -> list[JudgeVerdict]:
    """对一个 case 的回复跑 L3 judge。

    - case 没 judge_focus / 无可判维度 → 返回 [](本 case 不参与 L3)
    - 没配 ARK_API_KEY → 每个维度返回 skipped verdict(不烧 key)
    - 否则调豆包,解析返回

    product_facts:Agent 本轮实际拿到的 ProductSummary(由 runner 在 tool 外层捕获)。
    """
    dimensions = requested_dimensions(case)
    if not dimensions:
        return []
    if not judge_enabled():
        return [JudgeVerdict(d, True, "未配 ARK_API_KEY,L3 跳过", skipped=True) for d in dimensions]

    prompt = build_judge_prompt(
        query=case.get("query", ""),
        assistant_text=assistant_text,
        product_facts=product_facts,
        dimensions=dimensions,
    )
    try:
        client = _get_ark_client()
        resp = await client.chat.completions.create(
            model=config.DOUBAO_JUDGE_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=512,
        )
        raw = resp.choices[0].message.content or ""
    except Exception as e:  # noqa: BLE001 — judge 挂不拖垮整批
        log.warning("judge_call_failed", case_id=case.get("id"), error=str(e))
        return [JudgeVerdict(d, False, f"judge 调用失败: {type(e).__name__}", skipped=False) for d in dimensions]

    return parse_judge_response(raw, dimensions)


__all__ = [
    "JudgeVerdict",
    "requested_dimensions",
    "build_judge_prompt",
    "parse_judge_response",
    "judge_enabled",
    "judge_case",
]
