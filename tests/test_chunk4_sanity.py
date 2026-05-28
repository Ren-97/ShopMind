"""Chunk 4 Sanity Test — 本地纯逻辑 + 可选的 live LLM 调用。

覆盖:
- 所有 chunk4 模块 import 不挂
- Planner / Reranker tool_input_schema 来自 Pydantic,Pydantic 自校验
- Planner few-shot / Reranker few-shot 结构合法(role + content + tool_use 块)
- LLMReranker 的 _build_input / _format_candidate(纯字符串拼接,无 IO)
- 阈值过滤逻辑:RankerScoreItem → RankedProduct 阈值切线
- (可选)真打 Haiku:plan_query("敏感肌精华") + rank_candidates(...) — 需 ANTHROPIC_API_KEY

不测的:
- 端到端 retrieval(那是 chunk3 已覆盖 + chunk4 集成 eval 时跑)
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ──────────────────────────────────────────────────────────────
# import sanity
# ──────────────────────────────────────────────────────────────
def test_import_all_chunk4_modules() -> None:
    import server.llm.anthropic_client  # noqa: F401
    import server.llm.planner  # noqa: F401
    import server.llm.prompts  # noqa: F401
    import server.llm.prompts.planner  # noqa: F401
    import server.llm.prompts.reranker  # noqa: F401
    import server.llm.reranker  # noqa: F401
    import server.rag.reranking  # noqa: F401
    import server.rag.reranking.llm_reranker  # noqa: F401
    import server.rag.reranking.protocol  # noqa: F401


# ──────────────────────────────────────────────────────────────
# Tool input_schema 来自 Pydantic
# ──────────────────────────────────────────────────────────────
def test_planner_tool_schema_from_pydantic() -> None:
    from server.domain.types import QueryPlan
    from server.llm.planner import _QUERY_PLAN_TOOL  # type: ignore[attr-defined]

    schema = _QUERY_PLAN_TOOL["input_schema"]
    assert schema == QueryPlan.model_json_schema()
    # 关键字段在 schema 里
    assert "query_type" in schema["properties"]
    assert "hard_constraints" in schema["properties"]


def test_reranker_tool_schema_from_pydantic() -> None:
    from server.llm.reranker import _RANK_PRODUCTS_TOOL, RankerScores  # type: ignore[attr-defined]

    schema = _RANK_PRODUCTS_TOOL["input_schema"]
    assert schema == RankerScores.model_json_schema()
    assert "ranked" in schema["properties"]


# ──────────────────────────────────────────────────────────────
# Few-shot 结构合法
# ──────────────────────────────────────────────────────────────
def _check_messages_alternate(messages: list[dict]) -> None:
    """Anthropic 要求 user/assistant 交替。few-shot 应符合。"""
    # 简化:相邻两条不应同 role(允许 assistant 后接 user tool_result,role 仍是 user)
    for i in range(1, len(messages)):
        assert messages[i]["role"] != messages[i - 1]["role"], (
            f"role 不交替 @ {i}: {messages[i - 1]['role']} → {messages[i]['role']}"
        )


def test_planner_few_shot_structure() -> None:
    from server.llm.prompts.planner import PLANNER_FEW_SHOT_MESSAGES

    assert len(PLANNER_FEW_SHOT_MESSAGES) >= 8  # 至少 4 组 user/assistant + tool_result
    _check_messages_alternate(PLANNER_FEW_SHOT_MESSAGES)
    # assistant 块必须含 tool_use 名 make_query_plan
    for msg in PLANNER_FEW_SHOT_MESSAGES:
        if msg["role"] == "assistant":
            blocks = msg["content"]
            assert any(
                b.get("type") == "tool_use" and b.get("name") == "make_query_plan"
                for b in blocks
            )


def test_reranker_few_shot_structure() -> None:
    from server.llm.prompts.reranker import RERANKER_FEW_SHOT_MESSAGES

    assert len(RERANKER_FEW_SHOT_MESSAGES) >= 4
    _check_messages_alternate(RERANKER_FEW_SHOT_MESSAGES)
    for msg in RERANKER_FEW_SHOT_MESSAGES:
        if msg["role"] == "assistant":
            blocks = msg["content"]
            assert any(
                b.get("type") == "tool_use" and b.get("name") == "rank_products"
                for b in blocks
            )


# ──────────────────────────────────────────────────────────────
# Planner few-shot tool_input 可被 Pydantic 校验通过
# ──────────────────────────────────────────────────────────────
def test_planner_few_shot_inputs_validate() -> None:
    """每个 assistant tool_use input 都能过 QueryPlan 校验 — 防止 prompt 偏离 schema。"""
    from server.domain.types import QueryPlan
    from server.llm.prompts.planner import PLANNER_FEW_SHOT_MESSAGES

    for msg in PLANNER_FEW_SHOT_MESSAGES:
        if msg["role"] != "assistant":
            continue
        for block in msg["content"]:
            if block.get("type") == "tool_use" and block.get("name") == "make_query_plan":
                # 不能抛
                QueryPlan.model_validate(block["input"])


def test_reranker_few_shot_inputs_validate() -> None:
    from server.llm.prompts.reranker import RERANKER_FEW_SHOT_MESSAGES
    from server.llm.reranker import RankerScores

    for msg in RERANKER_FEW_SHOT_MESSAGES:
        if msg["role"] != "assistant":
            continue
        for block in msg["content"]:
            if block.get("type") == "tool_use" and block.get("name") == "rank_products":
                RankerScores.model_validate(block["input"])


# ──────────────────────────────────────────────────────────────
# LLMReranker 纯字符串拼接(无 IO)
# ──────────────────────────────────────────────────────────────
def test_llm_reranker_format_candidate() -> None:
    from server.domain.types import MatchedChunk, ProductHit
    from server.rag.reranking.llm_reranker import _format_candidate
    from server.storage.models import Product, ProductCaveats

    p = Product(
        product_id="p_x001",
        title="测试精华",
        brand="X-brand",
        category="美妆护肤",
        sub_category="精华",
        base_price=299.0,
        in_stock=True,
        is_active=True,
        properties={"suitable_skin": ["敏感肌"]},
    )
    p.caveats = ProductCaveats(
        product_id="p_x001", caveats_text="部分用户反馈瓶口设计"
    )

    hit = ProductHit(
        product_id="p_x001",
        score=0.9,
        matched_chunks=[
            MatchedChunk(
                chunk_id="c1", chunk_type="main", text="敏感肌温和保湿", score=0.9
            ),
            MatchedChunk(
                chunk_id="c2", chunk_type="review", text="用着不刺激", score=0.7
            ),
        ],
    )
    text = _format_candidate(p, hit)
    assert "p_x001" in text
    assert "测试精华" in text
    assert "敏感肌" in text
    assert "部分用户反馈瓶口设计" in text
    assert "[main]" in text and "[review]" in text


def test_llm_reranker_strip_main_prefix() -> None:
    """main chunk 文本去结构化前缀,只留 description。无 marker → 原样兜底。"""
    from server.rag.reranking.llm_reranker import _strip_main_prefix

    full = (
        "薇诺娜舒敏保湿特护霜\n"
        "品牌: 薇诺娜\n"
        "类目: 美妆护肤 > 面霜\n"
        "suitable_skin: 敏感肌、干皮\n"
        "描述: 薇诺娜舒敏保湿特护霜专为敏感肌打造,核心成分含马齿苋..."
    )
    out = _strip_main_prefix(full)
    assert out.startswith("薇诺娜舒敏保湿特护霜专为敏感肌打造")
    assert "品牌:" not in out
    assert "类目:" not in out

    # 无 marker → 原样
    assert _strip_main_prefix("没有描述前缀的纯文本") == "没有描述前缀的纯文本"


def test_llm_reranker_format_chunks_only_strips_main() -> None:
    """format_chunks 只对 chunk_type=='main' 调用 strip,其它原样。"""
    from server.domain.types import MatchedChunk
    from server.rag.reranking.llm_reranker import _format_chunks

    chunks = [
        MatchedChunk(
            chunk_id="m",
            chunk_type="main",
            text="title\n品牌: X\n描述: 真正的描述内容",
            score=0.9,
        ),
        MatchedChunk(
            chunk_id="r",
            chunk_type="review",
            text="用户评论:很好用",
            score=0.7,
        ),
    ]
    out = _format_chunks(chunks)
    assert "[main] 真正的描述内容" in out
    assert "[review] 用户评论:很好用" in out
    # 顶部结构化部分被 strip
    assert "品牌: X" not in out


def test_llm_reranker_build_input_shape() -> None:
    from server.domain.types import ProductHit
    from server.rag.reranking.llm_reranker import _build_input
    from server.storage.models import Product

    p = Product(
        product_id="p_x001", title="t", brand="b", category="c",
        sub_category="s", base_price=100.0, in_stock=True, is_active=True,
        properties={},
    )
    p.caveats = None
    hit = ProductHit(product_id="p_x001", score=0.5, matched_chunks=[])
    text = _build_input("query 测试", [(p, hit)])
    assert text.startswith("# Query: query 测试")
    assert "# Candidates:" in text
    assert "p_x001" in text


# ──────────────────────────────────────────────────────────────
# 阈值过滤逻辑(LLMReranker.rerank 末段) — 在内存里跑,不发 API
# ──────────────────────────────────────────────────────────────
def test_ranked_product_threshold_filter_logic() -> None:
    """模拟 LLM 已经返回 scores,验证阈值切线 + 排序 + 截顶。"""
    from server.domain.types import MatchedChunk
    from server.rag.reranking.protocol import RankedProduct

    items = [
        RankedProduct(
            product_id="p1", relevance_score=0.9, reason="strong",
            title="A", brand="X", category="C", sub_category="S",
            base_price=100.0, in_stock=True, properties={},
            matched_chunks=[],
        ),
        RankedProduct(
            product_id="p2", relevance_score=0.4, reason="weak",
            title="B", brand="X", category="C", sub_category="S",
            base_price=200.0, in_stock=True, properties={},
            matched_chunks=[
                MatchedChunk(chunk_id="c", chunk_type="main", text="t", score=0.4)
            ],
        ),
        RankedProduct(
            product_id="p3", relevance_score=0.65, reason="ok",
            title="C", brand="Y", category="C", sub_category="S",
            base_price=300.0, in_stock=True, properties={},
            matched_chunks=[],
        ),
    ]
    threshold = 0.5
    top_n = 2
    qualified = [r for r in items if r.relevance_score >= threshold]
    qualified.sort(key=lambda r: r.relevance_score, reverse=True)
    result = qualified[:top_n]
    assert [r.product_id for r in result] == ["p1", "p3"]
    # 阈值再调高 → no_match
    threshold2 = 0.99
    assert [r for r in items if r.relevance_score >= threshold2] == []


# ──────────────────────────────────────────────────────────────
# Live LLM(可选):显式开关 RUN_LIVE_LLM=1 才跑(避免 .env 里 key 失效 / 网络异常炸 CI)
# ──────────────────────────────────────────────────────────────
_HAS_ANTHROPIC = bool(os.getenv("ANTHROPIC_API_KEY")) and os.getenv("RUN_LIVE_LLM") == "1"


@pytest.mark.skipif(not _HAS_ANTHROPIC, reason="未开 RUN_LIVE_LLM=1,跳过 live 调用")
def test_planner_live_minimal() -> None:
    """端到端最小 case:Haiku 真打一次,验证输出落到合法 QueryPlan。"""
    from server.llm.planner import plan_query

    plan = asyncio.run(plan_query("敏感肌可用的保湿精华,预算 500"))
    assert plan.query_type in {
        "structured", "id_lookup", "filtered_semantic", "pure_semantic"
    }
    # 期望它分类到 filtered_semantic(主力)或 pure_semantic(若没识别到类目)
    # 软断言:至少 confidence 合法
    assert 0.0 <= plan.confidence <= 1.0


@pytest.mark.skipif(not _HAS_ANTHROPIC, reason="未开 RUN_LIVE_LLM=1,跳过 live 调用")
def test_reranker_live_minimal() -> None:
    """端到端最小 case:Haiku 评分,验证输出 score 在 [0,1] 且覆盖输入候选。"""
    from server.llm.reranker import rank_candidates

    text = """# Query: 敏感肌可用的保湿精华

# Candidates:
- product_id: p_live_001
  title: 雅诗兰黛特润修护精华
  brand: 雅诗兰黛
  category: 美妆护肤 / 精华
  price: 480
  in_stock: True
  tags: {"suitable_skin": ["敏感肌"], "effects": ["保湿"]}
  caveats: null
  matched_chunks:
    [main] 敏感肌可用,温和无酒精,深层保湿

- product_id: p_live_002
  title: 通用厨房收纳盒
  brand: 杂牌
  category: 家居 / 收纳
  price: 39
  in_stock: True
  tags: {}
  caveats: null
  matched_chunks:
    [main] 大容量收纳"""

    scores = asyncio.run(rank_candidates(text))
    ids = {s.product_id for s in scores.ranked}
    assert "p_live_001" in ids
    assert "p_live_002" in ids
    for s in scores.ranked:
        assert 0.0 <= s.relevance_score <= 1.0
    # 强匹配 vs 噪声 — 应有显著差距
    score_map = {s.product_id: s.relevance_score for s in scores.ranked}
    assert score_map["p_live_001"] > score_map["p_live_002"]


if __name__ == "__main__":
    test_import_all_chunk4_modules()
    test_planner_tool_schema_from_pydantic()
    test_reranker_tool_schema_from_pydantic()
    test_planner_few_shot_structure()
    test_reranker_few_shot_structure()
    test_planner_few_shot_inputs_validate()
    test_reranker_few_shot_inputs_validate()
    test_llm_reranker_format_candidate()
    test_llm_reranker_build_input_shape()
    test_ranked_product_threshold_filter_logic()
    print("[sanity] pure-logic chunk4 tests passed.")
