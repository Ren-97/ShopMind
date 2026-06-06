"""Ingest 主流程(§4.5.5 per-field diff + §4.5.6 失败恢复)。

执行入口:`scripts/ingest.py`(薄包装)。

主循环:
  1. 扫 INGEST_DATASET_DIR 下所有 *.json
  2. 对每个文件:parse → compute_content_hash → 对比 manifest
     - 无记录 / hash 不同 → sync_product(per-field diff)
     - hash 一致 → skip
  3. 单商品 fail → log + 跳过 + 不更新 manifest(下次自动重试)
  4. 末尾打印 Success/Failed 总结

同步策略:
- chunk_main:main_chunk_hash 变才重 embed(整文件 hash 变但 main 字段未变时短路)
- FAQ:任意变化 → 全删全建(每商品 3-5 个 faq,差分意义小)
- review:按 identity_hash 严格 diff(每商品 5+ 条,值得省 API)
- 类目特化属性:进 product.properties JSONB(GIN 索引)
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, Field
from qdrant_client import models as qmodels
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server import config
from server.indexing.brand_aliases import normalize_brand
from server.indexing.chunking import (
    build_faq_chunk_text,
    build_main_chunk_text,
    build_review_chunk_text,
    is_quality_review,
)
from server.indexing.manifest import (
    compute_main_chunk_hash,
    compute_product_content_hash,
    diff_by_identity,
    review_identity_hash,
)
from server.llm.review_sentiment import classify_review
from server.llm.review_summarizer import summarize_reviews
from server.rag.embedders import Embedder, get_embedder
from server.rag.sparse import JiebaBM25Encoder, SparseEncoder, SparseVector
from server.storage.catalog_repo import CatalogRepo
from server.storage.db import AsyncSessionLocal, create_all
from server.storage.manifest_repo import ManifestRepo
from server.storage.models import (
    Product,
    ProductFAQ,
    ProductReview,
    ProductReviewSummary,
    SKU,
)
from server.storage.vector_index import VectorIndex, get_vector_index

log = structlog.get_logger("shopmind.ingest")


# ═════════════════════════════════════════════════════════════
# JSON 解析(Pydantic v2)
# ═════════════════════════════════════════════════════════════
class SKUSpec(BaseModel):
    sku_id: str
    properties: dict[str, Any] = Field(default_factory=dict)
    price: float


class FAQSpec(BaseModel):
    question: str
    answer: str


class ReviewSpec(BaseModel):
    nickname: str
    rating: int
    content: str


class ProductSpec(BaseModel):
    product_id: str
    title: str
    brand: str
    category: str
    sub_category: str
    base_price: float
    image_path: str | None = None
    skus: list[SKUSpec] = Field(default_factory=list)
    marketing_description: str | None = None
    faqs: list[FAQSpec] = Field(default_factory=list)
    reviews: list[ReviewSpec] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)
    in_stock: bool = True
    is_active: bool = True


def parse_product_file(path: Path) -> tuple[ProductSpec, dict[str, Any]]:
    """读 JSON → ProductSpec + 原始 dict(用于算 content_hash)。"""
    raw = json.loads(path.read_text(encoding="utf-8"))
    rag = raw.get("rag_knowledge") or {}
    spec = ProductSpec(
        product_id=raw["product_id"],
        title=raw["title"],
        brand=normalize_brand(raw["brand"]),
        category=raw["category"],
        sub_category=raw["sub_category"],
        base_price=float(raw["base_price"]),
        image_path=raw.get("image_path"),
        skus=[SKUSpec(**s) for s in raw.get("skus", [])],
        marketing_description=rag.get("marketing_description"),
        faqs=[FAQSpec(**q) for q in rag.get("official_faq", [])],
        reviews=[ReviewSpec(**r) for r in rag.get("user_reviews", [])],
        properties=raw.get("tags", {}) or {},
        in_stock=bool(raw.get("in_stock", True)),
        is_active=bool(raw.get("is_active", True)),
    )
    return spec, raw


# ═════════════════════════════════════════════════════════════
# 工具:chunk_id → Qdrant point id(uuid5 deterministic)
# ═════════════════════════════════════════════════════════════
_NS = uuid.uuid5(uuid.NAMESPACE_URL, "shopmind://chunks")


def chunk_point_id(chunk_id: str) -> str:
    """从 chunk_id 派生稳定 UUID(Qdrant 要 UUID 或整数)。"""
    return str(uuid.uuid5(_NS, chunk_id))


# ═════════════════════════════════════════════════════════════
# 内部 chunk 描述(待 embed + upsert)
# ═════════════════════════════════════════════════════════════
@dataclass(slots=True)
class _ChunkToUpsert:
    chunk_id: str
    text: str
    payload: dict[str, Any]


# ═════════════════════════════════════════════════════════════
# Caveats 触发判断(§4.5.4)
# ═════════════════════════════════════════════════════════════
def _should_resummarize(
    existing: ProductReviewSummary | None,
    review_change_count: int,
    old_review_count: int,
) -> tuple[bool, str | None]:
    if existing is None:
        return True, "first_time"
    denom = old_review_count or 1
    if review_change_count / denom >= config.CAVEATS_REVIEW_CHANGE_RATIO:
        return True, "review_change_ratio"
    if existing.extracted_at is not None:
        extracted_at = existing.extracted_at
        if extracted_at.tzinfo is None:
            extracted_at = extracted_at.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(tz=timezone.utc) - extracted_at).days
        if age_days >= config.CAVEATS_MAX_AGE_DAYS:
            return True, "max_age_exceeded"
    return False, None


# ═════════════════════════════════════════════════════════════
# Qdrant Point 构造 + 批量 upsert
# ═════════════════════════════════════════════════════════════
async def _upsert_chunks(
    *,
    vector_index: VectorIndex,
    chunks: list[_ChunkToUpsert],
    embedder: Embedder,
    sparse_encoder: SparseEncoder,
) -> None:
    if not chunks:
        return
    texts = [c.text for c in chunks]
    dense_vectors = await embedder.embed_documents(texts)
    sparse_vectors = await sparse_encoder.encode(texts)
    points: list[qmodels.PointStruct] = []
    for chunk, dense, sparse in zip(chunks, dense_vectors, sparse_vectors, strict=True):
        points.append(
            qmodels.PointStruct(
                id=chunk_point_id(chunk.chunk_id),
                vector={
                    "dense": dense,
                    "sparse": qmodels.SparseVector(
                        indices=sparse.indices, values=sparse.values
                    ),
                },
                payload=chunk.payload,
            )
        )
    await vector_index.client.upsert(
        collection_name=vector_index.collection_name, points=points, wait=True
    )


async def _delete_points(
    vector_index: VectorIndex, chunk_ids: Sequence[str]
) -> None:
    if not chunk_ids:
        return
    point_ids = [chunk_point_id(cid) for cid in chunk_ids]
    await vector_index.client.delete(
        collection_name=vector_index.collection_name,
        points_selector=qmodels.PointIdsList(points=point_ids),
        wait=True,
    )


# ═════════════════════════════════════════════════════════════
# 主同步:单商品(§4.5.5 per-field diff)
# ═════════════════════════════════════════════════════════════
async def _sync_product(
    *,
    spec: ProductSpec,
    session: AsyncSession,
    vector_index: VectorIndex,
    embedder: Embedder,
    sparse_encoder: SparseEncoder,
    old_main_chunk_hash: str | None,
) -> tuple[int, str]:
    """同步单个 product;返回 (chunk_count, new_main_chunk_hash)。"""
    pid = spec.product_id

    # ── (1) Product 行:UPSERT(SQLAlchemy merge)──
    product = Product(
        product_id=pid,
        title=spec.title,
        brand=spec.brand,
        category=spec.category,
        sub_category=spec.sub_category,
        base_price=spec.base_price,
        image_path=spec.image_path,
        in_stock=spec.in_stock,
        is_active=spec.is_active,
        marketing_description=spec.marketing_description,
        # 所有类目特化属性进 JSONB 字段(suitable_skin / contains_alcohol /
        # gender / cpu / age_group 等,加新类目零 schema 改动)
        properties=dict(spec.properties),
    )
    await CatalogRepo.upsert_product(session, product)

    # ── (2) SKUs:replace-all(数量小、id 稳定,差分意义不大)──
    new_skus = [
        SKU(sku_id=s.sku_id, product_id=pid, properties=s.properties, price=s.price)
        for s in spec.skus
    ]
    await CatalogRepo.replace_skus(session, pid, new_skus)

    # ── (3) (已移除) Attributes 表 — 全部进 product.properties JSONB ──

    # ── (4) FAQs:任意变化 → 全替换 + Qdrant 删旧建新(V1 简化)──
    # FAQ 在商家定稿后基本不动,逐条 diff 收益基本为 0,故全替。
    old_faqs_stmt = select(ProductFAQ).where(ProductFAQ.product_id == pid)
    old_faqs = list((await session.execute(old_faqs_stmt)).scalars().all())
    old_faq_chunk_ids = [f"{pid}_faq_{f.order_idx}" for f in old_faqs]

    new_faq_rows = [
        ProductFAQ(product_id=pid, question=q.question, answer=q.answer, order_idx=idx)
        for idx, q in enumerate(spec.faqs)
    ]
    faqs_dirty = (
        [(f.question, f.answer) for f in old_faqs]
        != [(q.question, q.answer) for q in spec.faqs]
    )
    if faqs_dirty:
        await CatalogRepo.replace_faqs(session, pid, new_faq_rows)
        await _delete_points(vector_index, old_faq_chunk_ids)

    # ── (5) Reviews:identity-hash diff(每商品多条,值得精细差分)──
    old_reviews_stmt = select(ProductReview).where(ProductReview.product_id == pid)
    old_reviews = list((await session.execute(old_reviews_stmt)).scalars().all())
    old_review_idents: list[str] = []
    old_review_by_ident: dict[str, ProductReview] = {}
    for r in old_reviews:
        ident = review_identity_hash(nickname=r.nickname, rating=r.rating, content=r.content)
        old_review_idents.append(ident)
        old_review_by_ident.setdefault(ident, r)

    new_review_idents: list[str] = []
    new_review_by_ident: dict[str, ReviewSpec] = {}
    for r in spec.reviews:
        ident = review_identity_hash(nickname=r.nickname, rating=r.rating, content=r.content)
        new_review_idents.append(ident)
        new_review_by_ident.setdefault(ident, r)

    review_diff = diff_by_identity(old_review_idents, new_review_idents)

    # 删旧 SQL + 旧 Qdrant chunk
    removed_chunk_ids: list[str] = []
    for ident in review_diff.removed:
        old_row = old_review_by_ident.get(ident)
        if old_row is None:
            continue
        removed_chunk_ids.append(f"{pid}_review_{old_row.review_id}")
        await session.delete(old_row)
    await _delete_points(vector_index, removed_chunk_ids)

    # 插新 SQL(autoinc review_id 在 flush 后才有)
    new_row_by_ident: dict[str, ProductReview] = {}
    for ident in review_diff.new:
        spec_r = new_review_by_ident[ident]
        row = ProductReview(
            product_id=pid,
            nickname=spec_r.nickname,
            rating=spec_r.rating,
            content=spec_r.content,
        )
        session.add(row)
        new_row_by_ident[ident] = row
    await session.flush()  # 必须 flush 才能拿到 review_id

    # ── (6) 构建本次需要 upsert 的 chunks ──
    chunks_to_upsert: list[_ChunkToUpsert] = []

    # chunk_main:main_chunk_hash 变才重 embed
    # build_main_chunk_text 是 category-agnostic:遍历 properties 所有 key,
    # bool 跳过,list / 标量都进 chunk 文本(详见 main_chunker.py 注释)
    main_text = build_main_chunk_text(
        title=spec.title,
        brand=spec.brand,
        category=spec.category,
        sub_category=spec.sub_category,
        properties=spec.properties,
        marketing_description=spec.marketing_description,
    )
    new_main_chunk_hash = compute_main_chunk_hash(main_text)
    if new_main_chunk_hash != old_main_chunk_hash:
        chunks_to_upsert.append(
            _ChunkToUpsert(
                chunk_id=f"{pid}_main",
                text=main_text,
                payload={
                    "product_id": pid,
                    "chunk_type": "main",
                    "text": main_text,
                    "title": spec.title,
                    "brand": spec.brand,
                    "category": spec.category,
                    "sub_category": spec.sub_category,
                },
            )
        )

    # FAQ chunks:任一变化时全部重建
    if faqs_dirty:
        for row in new_faq_rows:
            fq_text = build_faq_chunk_text(row.question, row.answer)
            chunks_to_upsert.append(
                _ChunkToUpsert(
                    chunk_id=f"{pid}_faq_{row.order_idx}",
                    text=fq_text,
                    payload={
                        "product_id": pid,
                        "chunk_type": "faq",
                        "text": fq_text,
                        "order_idx": row.order_idx,
                    },
                )
            )

    # Review chunks:仅 NEW 的走 LLM sentiment + chunk + embed
    # 摘要分层采样需 sentiment 分池,收 (rating, content, sentiment) 三元组
    quality_reviews_for_summary: list[tuple[int, str, float | None]] = []
    for ident in review_diff.new:
        spec_r = new_review_by_ident[ident]
        if not is_quality_review(spec_r.content):
            continue  # SQL 已存,但不上 Qdrant(§4.5.2 Step 1)
        row = new_row_by_ident[ident]
        sentiment = await classify_review(spec_r.content)
        # 派生信号写回 SQL(单一真相源,ORM auto-UPDATE 在 commit 时执行)
        row.sentiment = sentiment.sentiment
        row.aspects = sentiment.aspects
        rv_text = build_review_chunk_text(
            rating=spec_r.rating, nickname=spec_r.nickname, content=spec_r.content
        )
        # Qdrant payload 从 SQL 行读字段(强调 SQL 是来源,Qdrant 是派生复刻)
        chunks_to_upsert.append(
            _ChunkToUpsert(
                chunk_id=f"{pid}_review_{row.review_id}",
                text=rv_text,
                payload={
                    "product_id": pid,
                    "chunk_type": "review",
                    "text": rv_text,
                    "rating": row.rating,
                    "nickname": row.nickname,
                    "sentiment": row.sentiment,
                    "aspects": row.aspects,
                },
            )
        )
        quality_reviews_for_summary.append((spec_r.rating, spec_r.content, sentiment.sentiment))

    # ── (7) 评论摘要:触发判断 → 抽 + 存 + chunk ──
    existing_summary_stmt = select(ProductReviewSummary).where(
        ProductReviewSummary.product_id == pid
    )
    existing_summary = (await session.execute(existing_summary_stmt)).scalar_one_or_none()

    needs_summary, reason = _should_resummarize(
        existing=existing_summary,
        review_change_count=review_diff.change_count,
        old_review_count=len(old_reviews),
    )
    if needs_summary:
        log.info("review_summary_triggered", product_id=pid, reason=reason)
        pool: list[tuple[int, str, float | None]] = list(quality_reviews_for_summary)
        if not pool:
            # 重抽场景:NEW 没有 quality,用现存 SQL reviews
            for r in old_reviews:
                if is_quality_review(r.content):
                    pool.append((r.rating, r.content, r.sentiment))
        # 分层采样:正/负分池各取上限,保证少数差评不被好评淹没
        negative: list[tuple[int, str]] = []
        positive: list[tuple[int, str]] = []
        for rating, content, sentiment_score in pool:
            is_negative = rating <= config.CAVEATS_NEGATIVE_RATING_MAX or (
                sentiment_score is not None
                and sentiment_score < config.CAVEATS_NEGATIVE_SENTIMENT_THRESHOLD
            )
            (negative if is_negative else positive).append((rating, content))
        sampled = (
            negative[: config.SUMMARY_MAX_NEGATIVE]
            + positive[: config.SUMMARY_MAX_POSITIVE]
        )
        try:
            result = await summarize_reviews(
                spec.title,
                sampled,
                total_reviews=len(pool),
                negative_reviews=len(negative),
            )
        except Exception as e:
            log.warning("review_summary_failed", product_id=pid, error=str(e))
            result = None

        if result is not None:
            await CatalogRepo.upsert_review_summary(
                session, pid, result.caveats_text, result.highlights
            )

    # caveats 只进 SQL(review_summary),不切 chunk:负面摘要进了向量库会被召回、漏进推荐侧
    # matched_chunks(推荐场景不下发缺点),还会污染召回("因为有这个毛病反而被搜出来")。
    # reranker / 对比 / 详情页 REST / 用户主动问(id_lookup)全部读 DB 字段,不依赖该 chunk。
    # 无条件删,清掉历史可能残留的 caveats 点(幂等)。
    await _delete_points(vector_index, [f"{pid}_caveats"])

    # ── (8) 批量 embed + upsert ──
    await _upsert_chunks(
        vector_index=vector_index,
        chunks=chunks_to_upsert,
        embedder=embedder,
        sparse_encoder=sparse_encoder,
    )

    # ── (9) 估算本商品当前 chunk_count(供 manifest 审计)──
    # quality_review_count = quality_reviews 数(NEW+UNCHANGED 中通过过滤的)
    quality_review_count = 0
    for ident in new_review_idents:
        spec_r = new_review_by_ident[ident]
        if is_quality_review(spec_r.content):
            quality_review_count += 1
    chunk_count = 1 + len(spec.faqs) + quality_review_count
    return chunk_count, new_main_chunk_hash


# ═════════════════════════════════════════════════════════════
# 主入口
# ═════════════════════════════════════════════════════════════
@dataclass(slots=True)
class IngestSummary:
    total_files: int = 0
    success: int = 0
    skipped_hash_match: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)  # (product_id_or_path, error)
    elapsed_sec: float = 0.0


def _scan_json_files(root: Path) -> list[Path]:
    """递归找所有 *.json,排序保证可复现。"""
    if not root.exists():
        raise FileNotFoundError(f"INGEST_DATASET_DIR 不存在: {root}")
    return sorted(root.rglob("*.json"))


async def ingest_all(
    *,
    embedder: Embedder | None = None,
    sparse_encoder: SparseEncoder | None = None,
) -> IngestSummary:
    """主入口:扫数据集 → 同步 SQL + Qdrant → 总结。"""
    started = time.perf_counter()
    summary = IngestSummary()

    # 基础设施就绪(幂等)
    await create_all()
    vector_index = get_vector_index()
    await vector_index.ensure_collection()

    # 默认实现:按 EMBEDDING_PROVIDER 选 Gemini / OpenAI + jieba BM25(lazy init)
    embedder = embedder or get_embedder()
    sparse_encoder = sparse_encoder or JiebaBM25Encoder()

    files = _scan_json_files(config.INGEST_DATASET_DIR_ABS)
    summary.total_files = len(files)
    log.info("ingest_start", root=str(config.INGEST_DATASET_DIR_ABS), files=len(files))

    for path in files:
        product_id_hint: str = path.stem
        try:
            spec, raw = parse_product_file(path)
            product_id_hint = spec.product_id
            content_hash = compute_product_content_hash(raw)

            async with AsyncSessionLocal() as session:
                existing = await ManifestRepo.get(session, spec.product_id)
                if existing is not None and existing.content_hash == content_hash:
                    summary.skipped_hash_match += 1
                    log.info("ingest_skip", product_id=spec.product_id, reason="hash_match")
                    continue

                old_main_hash = existing.main_chunk_hash if existing is not None else None
                chunk_count, new_main_hash = await _sync_product(
                    spec=spec,
                    session=session,
                    vector_index=vector_index,
                    embedder=embedder,
                    sparse_encoder=sparse_encoder,
                    old_main_chunk_hash=old_main_hash,
                )
                await ManifestRepo.upsert(
                    session,
                    product_id=spec.product_id,
                    content_hash=content_hash,
                    chunk_count=chunk_count,
                    main_chunk_hash=new_main_hash,
                )
                await session.commit()

            summary.success += 1
            log.info(
                "ingest_ok",
                product_id=spec.product_id,
                chunk_count=chunk_count,
            )
        except Exception as e:
            summary.failed.append((product_id_hint, str(e)))
            log.error(
                "ingest_failed",
                product_id=product_id_hint,
                file=str(path),
                error=str(e),
                exc_info=True,
            )

    summary.elapsed_sec = time.perf_counter() - started
    return summary


def print_summary(summary: IngestSummary) -> None:
    """终端友好的总结(对应 §4.5.6 末尾)。

    用 ASCII 标记(OK/SKIP/FAIL)而不是 ✓ ~ ✗ —— Windows 默认终端是 GBK
    编码,Unicode 几何符号会 UnicodeEncodeError(crash 在打印这一步,
    数据已经写入但用户以为 ingest 失败,体验差)。
    """
    print(f"\n== Ingest Summary ({summary.elapsed_sec:.1f}s) ==")
    print(f"  Total JSON files : {summary.total_files}")
    print(f"  [OK]   Success   : {summary.success}")
    print(f"  [SKIP] Hash match: {summary.skipped_hash_match}")
    print(f"  [FAIL] Failed    : {len(summary.failed)}")
    if summary.failed:
        print("\nFailed products:")
        for pid, err in summary.failed:
            print(f"  - {pid}: {err}")
        print("\n下次跑 `python scripts/ingest.py` 会自动重试失败项。")


__all__ = [
    "ProductSpec",
    "parse_product_file",
    "ingest_all",
    "print_summary",
    "IngestSummary",
    "chunk_point_id",
]
