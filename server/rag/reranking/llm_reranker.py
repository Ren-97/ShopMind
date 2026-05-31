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

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from server import config
from server.domain.types import MatchedChunk, ProductHit
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
    caveats_text = product.caveats.caveats_text if product.caveats else None
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
    caveats_text = product.caveats.caveats_text if product.caveats else None
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
        matched_chunks=list(hit.matched_chunks),
    )


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
        self, query: str, hits: list[ProductHit]
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

        # 5) 阈值过滤 + 排序 + 截顶
        qualified = [r for r in ranked if r.relevance_score >= self._threshold]
        qualified.sort(key=lambda r: r.relevance_score, reverse=True)
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
