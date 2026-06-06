"""LLMReranker(§4.3)。

流程:
  1. ProductHit 列表 → DB enrich(title / brand / category / price / properties / caveats)
  2. 拼 `# Query: ... # Candidates: ...` 文本(每个候选含 matched_chunks 原文)
  3. 调 `server.llm.reranker.rank_candidates` → LLM 评分
  4. 合并评分 + DB enrich → RankedProduct
  5. 阈值过滤(score ≥ RERANK_THRESHOLD)+ 排序 + 截 top_n
  6. 空 → no_match,上游 Agent 触发兜底文案

DB enrich 失败(product 不在 catalog / 已下架)→ 跳过该候选,不影响其它。
LLM Hard Fail(RerankerError)→ 透传上抛(§4.2.7 同款 Hard Fail 路径,不构造降级排序)。
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from server import config
from server.domain.types import MatchedChunk, ProductHit
from server.indexing.brand_aliases import normalize_brand
from server.llm.reranker import RankerScoreItem, rank_candidates
from server.rag.reranking.protocol import RankedProduct
from server.storage.catalog_repo import CatalogRepo
from server.storage.models import Product

log = structlog.get_logger("shopmind.reranker.llm")


# main chunk 文本里 description 段的固定分隔符(由 server/indexing/chunking/main_chunker.py
# `build_main_chunk_text` 写入)。改 chunker 格式时这两边要同步。
_MAIN_DESCRIPTION_MARKER: str = "描述: "


def _truncate(text: str, limit: int | None = None) -> str:
    cap = limit if limit is not None else config.MATCHED_CHUNK_TEXT_MAX_CHARS
    if len(text) <= cap:
        return text
    return text[: cap - 1] + "…"


def _strip_main_prefix(text: str) -> str:
    """main chunk 文本去掉结构化前缀(title / brand / 类目 / properties),只留 description。

    上游 chunker 拼 main chunk 时,结构化部分在前、`描述: ...` 在后(详见
    `build_main_chunk_text`)。reranker 顶层字段已经把结构化部分以更清洁的形式
    展示给 LLM,这里 strip 一层避免冗余。无 marker(数据缺失)→ 原样返回兜底。
    """
    idx = text.find(_MAIN_DESCRIPTION_MARKER)
    if idx == -1:
        return text
    return text[idx + len(_MAIN_DESCRIPTION_MARKER):]


def _format_chunks(chunks: list[MatchedChunk]) -> str:
    """每个 chunk 一行,带类型前缀,文本截断。main chunk 额外 strip 结构化前缀。"""
    if not chunks:
        return "    (无)"
    lines: list[str] = []
    for c in chunks:
        raw = _strip_main_prefix(c.text) if c.chunk_type == "main" else c.text
        lines.append(f"    [{c.chunk_type}] {_truncate(raw)}")
    return "\n".join(lines)


def _format_candidate(product: Product, hit: ProductHit) -> str:
    """单个候选拼成 markdown 块。caveats 走关联表(可为 None)。"""
    caveats_text = product.review_summary.caveats_text if product.review_summary else None
    return (
        f"- product_id: {product.product_id}\n"
        f"  title: {product.title}\n"
        f"  brand: {product.brand}\n"
        f"  category: {product.category} / {product.sub_category}\n"
        f"  price: {product.base_price}\n"
        f"  in_stock: {product.in_stock}\n"
        f"  tags: {product.properties}\n"
        f"  caveats: {caveats_text}\n"
        f"  matched_chunks:\n{_format_chunks(hit.matched_chunks)}"
    )


def _build_input(query: str, pairs: list[tuple[Product, ProductHit]]) -> str:
    candidates_text = "\n\n".join(_format_candidate(p, h) for p, h in pairs)
    return f"# Query: {query}\n\n# Candidates:\n{candidates_text}"


def _to_ranked_product(
    product: Product, hit: ProductHit, score: RankerScoreItem
) -> RankedProduct:
    caveats_text = product.review_summary.caveats_text if product.review_summary else None
    return RankedProduct(
        product_id=product.product_id,
        relevance_score=score.relevance_score,
        reason=score.reason,
        title=product.title,
        brand=product.brand,
        category=product.category,
        sub_category=product.sub_category,
        base_price=product.base_price,
        in_stock=product.in_stock,
        image_path=product.image_path,
        properties=dict(product.properties or {}),
        caveats_text=caveats_text,
        skus=[
            {
                "sku_id": s.sku_id,
                "properties": dict(s.properties or {}),
                "price": float(s.price),
            }
            for s in product.skus
        ],
        matched_chunks=list(hit.matched_chunks),
    )


# profile.gender(英文)→ 商品 properties.gender(中文)映射
_GENDER_MAP: dict[str, str] = {"female": "女", "male": "男", "女": "女", "男": "男"}

# profile.os_pref → 对应品牌(系统偏好没有结构化字段,落到品牌这个结构化维度)
_OS_BRAND: dict[str, str] = {"iOS": "Apple 苹果"}


def _age_bucket(age: Any) -> str | None:
    """profile.age(具体岁数)→ 商品 age_group 档位(取 ≤age 的最近档)。"""
    if not isinstance(age, (int, float)):
        return None
    if age >= 30:
        return "30+"
    if age >= 25:
        return "25+"
    if age >= 20:
        return "20+"
    return None


def _profile_fit_boost(product: RankedProduct, profile: dict[str, Any] | None) -> float:
    """确定性个性化加分(§4.3 profile resort 层)。读 product.properties / brand 对**画像里
    结构化可映射的偏好**打分,返回封顶 ±`PROFILE_FIT_BOOST_CAP` 的微调量。

    设计:relevance(LLM 打的分)是主维度;本函数只在**已过阈值的合格集**内做同档排序微调
    (适合用户的往前提),**不淘汰任何商品** —— 调用方在 threshold 过滤之后才加它,合格集已锁定。

    **只处理结构化 / 可精确映射的偏好**(闭集字段、品牌、os→品牌)。usage / style / dietary 这类
    "开放同义词"偏好(用户说"摄影"、商品标"拍照")精确规则匹配不到、又无干净结构化字段 → V1 不做个性化
    (要做得靠语义匹配,V2 再议)。命中信号(各 ±PROFILE_FIT_SIGNAL_WEIGHT,求和后封顶):
    肤质适配(+)/明确不适配(−)、偏好品牌(+)、系统偏好→品牌(iOS⇒Apple,+)、
    无香诉求一致(+)、年龄段(+)、性别(+)、护肤诉求∩商品功效(+)。
    """
    if not profile:
        return 0.0
    prefs = profile.get("preferences") or {}
    props = product.properties or {}
    w = config.PROFILE_FIT_SIGNAL_WEIGHT
    score = 0.0

    # 肤质:适配 + / 明确不适配 −(不适配只排后,不淘汰)
    skin = prefs.get("skin_type")
    if skin:
        if skin in (props.get("suitable_skin") or []):
            score += w
        elif skin in (props.get("not_suitable_skin") or []):
            score -= w

    # 偏好品牌(normalize 后比较,容忍别名)
    brand_prefer = {normalize_brand(b) for b in (prefs.get("brand_prefer") or []) if b}
    if brand_prefer and normalize_brand(product.brand) in brand_prefer:
        score += w

    # 系统偏好 → 品牌(iOS ⇒ Apple);os_pref 没有结构化商品字段,落到品牌维度
    os_brand = _OS_BRAND.get(str(prefs.get("os_pref") or ""))
    if os_brand and normalize_brand(product.brand) == normalize_brand(os_brand):
        score += w

    # 无香诉求 ↔ 商品无香
    if prefs.get("fragrance_pref") == "无香" and props.get("contains_fragrance") is False:
        score += w

    # 年龄段 / 性别:精确档位匹配才加,"通用"=中性不加(避免无差别加分)
    bucket = _age_bucket(profile.get("age"))
    if bucket and props.get("age_group") == bucket:
        score += w
    gender = _GENDER_MAP.get(str(profile.get("gender") or ""))
    if gender and props.get("gender") == gender:
        score += w

    # 护肤诉求 ∩ 商品功效(保湿 / 舒缓 …)
    concerns = set(prefs.get("skin_concerns") or [])
    if concerns and concerns & set(props.get("effects") or []):
        score += w

    cap = config.PROFILE_FIT_BOOST_CAP
    return max(-cap, min(cap, score))


class LLMReranker:
    """V1 默认 Reranker:Claude Haiku 4.5 + Tool Use(§4.3.2)。"""

    name = "llm_haiku_rerank"

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        threshold: float | None = None,
        top_n: int | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._threshold = (
            threshold if threshold is not None else config.RERANK_THRESHOLD
        )
        self._top_n = top_n if top_n is not None else config.RERANK_TOP_N

    async def _enrich(
        self, hits: list[ProductHit]
    ) -> list[tuple[Product, ProductHit]]:
        """按 product_id 批量 fetch + 关联 SKU/FAQ/Reviews/Caveats。

        - 永远 `is_active=TRUE`(防幻觉铁律 1,`get_product_with_details` 默认带)
        - 顺序保持与 hits 一致(rerank 输入相对顺序对 LLM context 友好)
        - 缺失的 product_id(已下架或被删)直接跳过,LLM 看到的就是干净候选
        """
        async with self._session_factory() as session:
            pairs: list[tuple[Product, ProductHit]] = []
            for hit in hits:
                product = await CatalogRepo.get_product_with_details(
                    session, hit.product_id
                )
                if product is None:
                    log.warning(
                        "reranker_enrich_missing",
                        product_id=hit.product_id,
                        reason="not_found_or_inactive",
                    )
                    continue
                pairs.append((product, hit))
        return pairs

    async def rerank(
        self,
        query: str,
        hits: list[ProductHit],
        *,
        profile: dict[str, Any] | None = None,
    ) -> list[RankedProduct]:
        if not hits:
            log.info("reranker_skip", reason="empty_hits")
            return []

        # 1) DB enrich
        pairs = await self._enrich(hits)
        if not pairs:
            log.info("reranker_no_candidates_after_enrich", n_input=len(hits))
            return []

        # 2) 拼 LLM 输入
        formatted = _build_input(query, pairs)

        # 3) LLM 评分(异常透传)
        scores = await rank_candidates(formatted)

        # 4) 合并(product_id → score 索引)
        score_by_pid: dict[str, RankerScoreItem] = {
            s.product_id: s for s in scores.ranked
        }

        ranked: list[RankedProduct] = []
        for product, hit in pairs:
            score = score_by_pid.get(product.product_id)
            if score is None:
                log.warning(
                    "reranker_missing_score_for_candidate",
                    product_id=product.product_id,
                )
                continue
            ranked.append(_to_ranked_product(product, hit, score))

        # 5) 阈值过滤 → 个性化 fit 重排(确定性,只在合格集内排序、不淘汰)→ 截顶
        # fit 加在 threshold 过滤**之后** → 合格集已锁定,加分只影响排序 + 谁进 top_n,绝不踢人
        # (浏览型 query 的安全垫)。relevance 仍是主维度,fit 顶多 ±PROFILE_FIT_BOOST_CAP 同档微调。
        qualified = [r for r in ranked if r.relevance_score >= self._threshold]
        qualified.sort(
            key=lambda r: r.relevance_score + _profile_fit_boost(r, profile),
            reverse=True,
        )
        result = qualified[: self._top_n]

        log.info(
            "reranker_done",
            n_input=len(hits),
            n_enriched=len(pairs),
            n_scored=len(ranked),
            n_qualified=len(qualified),
            n_returned=len(result),
            threshold=self._threshold,
            top_n=self._top_n,
            no_match=len(result) == 0,
        )
        return result


__all__ = ["LLMReranker"]
