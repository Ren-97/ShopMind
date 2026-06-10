"""检索管线诊断 —— 排查「推荐只剩 1 款」到底卡在哪一段。

跑法:`uv run python scripts/diag_search.py`(可与 uvicorn 同时跑;若 Qdrant 被
服务独占,rerank 段会跳过,但 planner + SQL 段照样输出——那两段已足够定位
「是不是 planner 还在把画像偏好当硬过滤」)。

看三件事:
1. **加载的文件路径**:确认跑的就是你编辑的工作树,而不是别处的副本。
2. **PLAN.hard_constraints.suitable_skin**:空 = 画像肤质走 soft(已修);非空 = 还在硬过滤(旧代码)。
3. **每段还剩几款**:SQL 白名单 / 召回 / rerank,一眼看出在哪一段被砍。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import server.llm.prompts.planner as planner_prompt
import server.tools.search as search_mod
from server import config
from server.llm.planner import plan_query
from server.storage.catalog_repo import CatalogRepo
from server.storage.db import AsyncSessionLocal
from server.storage.user_repo import UserRepo

USER_ID = "demo_user_1"
QUERY = "我想买精华"


async def main() -> None:
    print("== 加载的文件路径(确认是你编辑的工作树)==")
    print("  search.py :", search_mod.__file__)
    print("  planner.py:", planner_prompt.__file__)
    # few-shot 里是否已含「按产地反选(origin_exclude)」示例
    has_origin = any(
        "origin_exclude" in json.dumps(m, ensure_ascii=False)
        for m in planner_prompt.PLANNER_FEW_SHOT_MESSAGES
    )
    print("  planner few-shot 含 origin_exclude 反选示例:", has_origin)

    async with AsyncSessionLocal() as s:
        p = await UserRepo.get_profile(s, USER_ID)
        profile = {
            k: v
            for k, v in {
                "age": p.age,
                "gender": p.gender,
                "consumption_tier": p.consumption_tier,
                "preferences": p.preferences,
            }.items()
            if v is not None and v != {}
        } if p else {}
    print("\n== PROFILE ==")
    print(" ", json.dumps(profile, ensure_ascii=False))

    # 与生产一致:Planner 不收 profile(画像走代码合并 + reranker)
    plan = await plan_query(QUERY, session_state=None)
    print("\n== PLAN ==")
    print("  hard:", json.dumps(plan.hard_constraints.model_dump(), ensure_ascii=False))
    print("  suitable_skin 是否为空(应为空):", not plan.hard_constraints.suitable_skin)
    print("  text_query:", plan.text_query)

    # 统一合并所有反选来源(query/origin/session.rejected/profile)→ hard
    plan, excluded_brands = search_mod._apply_exclusions(
        plan, profile=profile, session_snapshot=None
    )
    print("\n== _apply_exclusions 后 ==")
    print("  brand_exclude(hard):", plan.hard_constraints.brand_exclude)
    print("  excluded_brands(guard 用):", sorted(excluded_brands))
    print("  text_query(未增强):", plan.text_query)

    async with AsyncSessionLocal() as s:
        wl = await CatalogRepo.list_product_ids_by_constraints(s, plan.hard_constraints)
    print(f"\n[1] SQL 白名单: {len(wl)} 款 -> {sorted(wl)}")

    # rerank 段需要 Qdrant;服务在跑时会被锁,失败就跳过(前面已足够定位)
    try:
        from server.cache.in_memory import InMemoryLRUCache
        from server.rag.embedders import get_embedder
        from server.rag.embedders.cached import CachedEmbedder
        from server.rag.reranking.llm_reranker import LLMReranker
        from server.rag.retrieval.dispatcher import build_default_dispatcher
        from server.rag.sparse import JiebaBM25Encoder
        from server.storage.vector_index import get_vector_index

        embedder = CachedEmbedder(get_embedder(), InMemoryLRUCache(maxsize=100, ttl=None))
        dispatcher = build_default_dispatcher(
            session_factory=AsyncSessionLocal,
            vector_index=get_vector_index(),
            embedder=embedder,
            sparse_encoder=JiebaBM25Encoder(),
            cache=None,
        )
        res = await dispatcher.dispatch(plan)
        print(f"[2] 召回候选: {len(res.products)} 款")
        ranked = await LLMReranker(session_factory=AsyncSessionLocal).rerank(
            plan.text_query or QUERY, res.products
        )
        print(
            f"[3] rerank 后: {len(ranked)} 款 "
            f"(阈值={config.RERANK_THRESHOLD}, top_n={config.RERANK_TOP_N})"
        )
        for r in ranked:
            print(f"     {r.product_id}  {r.title[:18]}  score={r.relevance_score}")
    except Exception as e:  # noqa: BLE001 — Qdrant 被服务独占时预期失败,跳过即可
        print(f"\n[2/3] 跳过(Qdrant 被占用?): {type(e).__name__}")
        print("      planner + SQL 段已足够判断:若上面 suitable_skin 为空、SQL 白名单>1,")
        print("      则后端代码是对的;App 还只给 1 款 = App 连的不是这个后端。")


if __name__ == "__main__":
    asyncio.run(main())
