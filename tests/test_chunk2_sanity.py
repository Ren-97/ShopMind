"""Chunk 2 Sanity Test — 纯本地,无网络 / 无 API key。

只测纯函数 & import:
- 所有 chunk2 模块 import 不挂
- 4 个 chunker 文本拼接对得上 spec
- review 规则过滤行为正确
- content_hash 稳定 + diff helper 三集划分正确
- Pydantic ProductSpec 解析真实 sample JSON 不挂
- caveats / sentiment LLM 模块**不调 API**,只测 import + 数据契约

跑 ingest 真把 100 商品入库 = 端到端测,需要 .env + API key,在 README 描述但不在此跑。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ──────────────────────────────────────────────────────────────
# import sanity
# ──────────────────────────────────────────────────────────────
def test_import_all_chunk2_modules() -> None:
    import server.indexing  # noqa: F401
    import server.indexing.chunking  # noqa: F401
    import server.indexing.chunking.caveats_chunker  # noqa: F401
    import server.indexing.chunking.faq_chunker  # noqa: F401
    import server.indexing.chunking.main_chunker  # noqa: F401
    import server.indexing.chunking.review_chunker  # noqa: F401
    import server.indexing.ingest  # noqa: F401
    import server.indexing.manifest  # noqa: F401
    import server.llm  # noqa: F401
    import server.llm.anthropic_client  # noqa: F401
    import server.llm.caveats_extractor  # noqa: F401
    import server.llm.review_sentiment  # noqa: F401
    import server.rag.embedders  # noqa: F401
    import server.rag.embedders.protocol  # noqa: F401
    import server.rag.sparse  # noqa: F401
    import server.rag.sparse.protocol  # noqa: F401
    import scripts.ingest  # noqa: F401


# ──────────────────────────────────────────────────────────────
# Chunker:文本拼接(无网络)
# ──────────────────────────────────────────────────────────────
def test_main_chunker_text_layout() -> None:
    """美妆类目:list + 标量 + 描述都进 chunk;bool 字段(contains_*)跳过。"""
    from server.indexing.chunking import build_main_chunk_text

    text = build_main_chunk_text(
        title="某某精华 30ml",
        brand="X品牌",
        category="美妆护肤",
        sub_category="精华",
        properties={
            "suitable_skin": ["干皮", "中性肌"],
            "not_suitable_skin": ["敏感肌"],
            "effects": ["抗老", "保湿"],
            "scene": ["夜用"],
            "age_group": "25+",
            "contains_alcohol": False,   # ★ bool → 不进 chunk
            "contains_fragrance": True,  # ★ bool → 不进 chunk
        },
        marketing_description="主打夜间修护。",
    )
    assert text.startswith("某某精华 30ml")
    assert "品牌: X品牌" in text
    assert "类目: 美妆护肤 > 精华" in text
    # 关键 token 必须出现(BM25 / Dense 召回靠这些)
    assert "干皮" in text and "中性肌" in text
    assert "敏感肌" in text
    assert "抗老" in text and "保湿" in text
    assert "夜用" in text
    assert "25+" in text
    assert "描述: 主打夜间修护。" in text
    # bool 字段不出现
    assert "contains_alcohol" not in text
    assert "contains_fragrance" not in text


def test_main_chunker_works_across_categories() -> None:
    """跨类目通用:服饰 / 数码 / 食品 都能正确生成,无需改 main_chunker。"""
    from server.indexing.chunking import build_main_chunk_text

    # 服饰类:gender 标量 + effects/scene list
    clothes_text = build_main_chunk_text(
        title="背包", brand="Y", category="服饰运动", sub_category="背包",
        properties={"effects": ["轻量"], "gender": "通用", "scene": ["通勤"]},
        marketing_description=None,
    )
    assert "轻量" in clothes_text
    assert "通用" in clothes_text  # gender 标量
    assert "通勤" in clothes_text
    assert "描述:" not in clothes_text  # marketing_description=None

    # 数码类:未来的 cpu 字段(测试 generic 接受任意 key)
    digital_text = build_main_chunk_text(
        title="平板", brand="Z", category="数码电子", sub_category="平板电脑",
        properties={"effects": ["大屏"], "cpu": "i7", "screen_size": 12.6},
        marketing_description=None,
    )
    assert "大屏" in digital_text
    assert "i7" in digital_text         # 任意标量都进
    assert "12.6" in digital_text       # int/float 也支持


def test_main_chunker_skips_empty_and_none() -> None:
    """空 list / None / 空字符串 全部跳过,不生成空行。"""
    from server.indexing.chunking import build_main_chunk_text

    text = build_main_chunk_text(
        title="测试", brand="X", category="食品生活", sub_category="零食",
        properties={
            "effects": [],          # 空 list
            "gender": None,         # None
            "age_group": "",        # 空字符串
            "scene": ["佐餐"],
        },
        marketing_description=None,
    )
    assert "effects" not in text
    assert "gender" not in text
    assert "age_group" not in text
    assert "佐餐" in text


def test_faq_chunker() -> None:
    from server.indexing.chunking import build_faq_chunk_text

    assert build_faq_chunk_text("怎么用?", "洁面后使用。") == "问: 怎么用?\n答: 洁面后使用。"


def test_review_chunker_text() -> None:
    from server.indexing.chunking import build_review_chunk_text

    assert (
        build_review_chunk_text(rating=5, nickname="阿凯", content="超好用")
        == "[5星] 阿凯: 超好用"
    )


def test_caveats_chunker() -> None:
    from server.indexing.chunking import build_caveats_chunk_text

    text = build_caveats_chunk_text("部分敏感肌用户反馈刺痛。")
    assert text.startswith("⚠️ 注意:")
    assert "部分敏感肌用户反馈刺痛。" in text


# ──────────────────────────────────────────────────────────────
# Review 规则过滤(§4.5.2 Step 1)
# ──────────────────────────────────────────────────────────────
def test_is_quality_review_filters_short_text() -> None:
    from server.indexing.chunking import is_quality_review

    assert not is_quality_review("不错")  # 太短
    assert not is_quality_review("")  # 空


def test_is_quality_review_filters_dup_chars() -> None:
    from server.indexing.chunking import is_quality_review

    assert not is_quality_review("好好好好好好好好好好好好好好好好好好好好")  # 全同字符


def test_is_quality_review_requires_chinese() -> None:
    from server.indexing.chunking import is_quality_review

    assert not is_quality_review("This is a long enough review without any chinese chars.")


def test_is_quality_review_accepts_normal_chinese() -> None:
    from server.indexing.chunking import is_quality_review

    assert is_quality_review("用了两次脸颊就开始泛红刺痛,敏感肌真的需要先测试再用。")


# ──────────────────────────────────────────────────────────────
# Manifest:hash + diff
# ──────────────────────────────────────────────────────────────
def test_product_content_hash_stable_across_key_order() -> None:
    from server.indexing.manifest import compute_product_content_hash

    a = {"product_id": "p1", "title": "T", "extra": [1, 2, 3]}
    b = {"extra": [1, 2, 3], "title": "T", "product_id": "p1"}
    assert compute_product_content_hash(a) == compute_product_content_hash(b)


def test_product_content_hash_detects_changes() -> None:
    from server.indexing.manifest import compute_product_content_hash

    base = {"product_id": "p1", "base_price": 100.0}
    changed = {"product_id": "p1", "base_price": 110.0}
    assert compute_product_content_hash(base) != compute_product_content_hash(changed)


def test_main_chunk_hash_stable_and_sensitive() -> None:
    """main_chunk_hash 同文本稳定、内容变即变 — 短路 re-embed 的正确性前提。"""
    from server.indexing.manifest import compute_main_chunk_hash

    h1 = compute_main_chunk_hash("某某精华\n品牌: X\n类目: 美妆 > 精华")
    h2 = compute_main_chunk_hash("某某精华\n品牌: X\n类目: 美妆 > 精华")
    h3 = compute_main_chunk_hash("某某精华\n品牌: Y\n类目: 美妆 > 精华")
    assert h1 == h2
    assert h1 != h3


def test_review_identity_hash_is_content_sensitive() -> None:
    from server.indexing.manifest import review_identity_hash

    h1 = review_identity_hash(nickname="A", rating=5, content="好用")
    h2 = review_identity_hash(nickname="A", rating=5, content="不好用")
    h3 = review_identity_hash(nickname="A", rating=5, content="好用")
    assert h1 != h2
    assert h1 == h3


def test_diff_by_identity_splits_three_categories() -> None:
    from server.indexing.manifest import diff_by_identity

    old = ["a", "b", "c"]
    new = ["b", "c", "d"]
    result = diff_by_identity(old, new)
    assert set(result.new) == {"d"}
    assert set(result.removed) == {"a"}
    assert set(result.unchanged) == {"b", "c"}
    assert result.change_count == 2


def test_diff_by_identity_first_time() -> None:
    """首次 ingest:old 空 → 全是 NEW。"""
    from server.indexing.manifest import diff_by_identity

    result = diff_by_identity([], ["x", "y"])
    assert result.new == ["x", "y"]
    assert result.removed == []
    assert result.unchanged == []


def test_diff_by_identity_no_change() -> None:
    """hash 一致 / 内容完全相同 → 全 UNCHANGED。"""
    from server.indexing.manifest import diff_by_identity

    result = diff_by_identity(["x", "y"], ["x", "y"])
    assert result.unchanged == ["x", "y"]
    assert result.new == []
    assert result.removed == []


# ──────────────────────────────────────────────────────────────
# ProductSpec:能解 sample JSON
# ──────────────────────────────────────────────────────────────
def test_parse_real_sample_files() -> None:
    """6 个 sample JSON 都能解出 ProductSpec(不跑 LLM / 不写 DB)。"""
    from server import config
    from server.indexing.ingest import parse_product_file

    files = sorted(Path(config.INGEST_DATASET_DIR_ABS).rglob("*.json"))
    assert len(files) > 0, f"无 sample JSON: {config.INGEST_DATASET_DIR_ABS}"
    for path in files:
        spec, raw = parse_product_file(path)
        assert spec.product_id == raw["product_id"]
        assert spec.title
        assert spec.brand
        assert spec.skus, f"{spec.product_id} 应至少有一个 SKU"


def test_parse_maps_tags_to_properties() -> None:
    """JSON 字段 `tags` 解析进 ProductSpec.properties。"""
    from server import config
    from server.indexing.ingest import parse_product_file

    files = sorted(Path(config.INGEST_DATASET_DIR_ABS).rglob("*.json"))
    found_with_tags = False
    for path in files:
        spec, raw = parse_product_file(path)
        if raw.get("tags"):
            found_with_tags = True
            assert spec.properties == raw["tags"]
    assert found_with_tags


def test_main_chunker_call_signature() -> None:
    """build_main_chunk_text 关键参数签名校验,防 ingest 调用错参数名。"""
    import inspect

    from server.indexing.chunking import build_main_chunk_text

    params = set(inspect.signature(build_main_chunk_text).parameters.keys())
    assert {"title", "brand", "category", "sub_category",
            "properties", "marketing_description"}.issubset(params)


def test_chunk_point_id_deterministic_uuid() -> None:
    """同 chunk_id 派生同 UUID(Qdrant 幂等关键)。"""
    from server.indexing.ingest import chunk_point_id

    a = chunk_point_id("p_beauty_001_main")
    b = chunk_point_id("p_beauty_001_main")
    c = chunk_point_id("p_beauty_001_review_5")
    assert a == b
    assert a != c
    # UUID 格式
    assert len(a) == 36 and a.count("-") == 4


# ──────────────────────────────────────────────────────────────
# LLM 模块:只校验契约,**不调真 API**
# ──────────────────────────────────────────────────────────────
def test_review_sentiment_result_schema() -> None:
    from server.llm.review_sentiment import ReviewSentimentResult

    r = ReviewSentimentResult(sentiment=-0.8, aspects=["敏感肌"])
    assert r.sentiment == -0.8
    assert r.aspects == ["敏感肌"]

    with pytest.raises(Exception):
        ReviewSentimentResult(sentiment=2.0, aspects=[])  # 超界


def test_caveats_result_schema() -> None:
    from server.llm.caveats_extractor import CaveatsResult

    r = CaveatsResult(caveats_text=None, confidence=0.0)
    assert r.caveats_text is None

    r2 = CaveatsResult(caveats_text="部分用户反馈刺痛", confidence=0.7)
    assert r2.confidence == 0.7


@pytest.mark.asyncio
async def test_extract_caveats_empty_reviews_no_api_call() -> None:
    """空 reviews → 直接返回 (null, 0.0),不发请求。"""
    from server.llm.caveats_extractor import extract_caveats

    result = await extract_caveats("某商品", [])
    assert result.caveats_text is None
    assert result.confidence == 0.0


# ──────────────────────────────────────────────────────────────
# Schema:ProductReview 新列 sentiment / aspects 可读写(Postgres)
# ──────────────────────────────────────────────────────────────
async def test_review_sentiment_columns_roundtrip(test_session_factory) -> None:
    """sentiment + aspects 写进 SQL,读回来一致。"""
    from sqlalchemy import select

    from server.storage.models import Product, ProductReview

    async with test_session_factory() as session:
        session.add(
            Product(
                product_id="p_test",
                title="T",
                brand="B",
                category="C",
                sub_category="S",
                base_price=10.0,
                in_stock=True,
                is_active=True,
                properties={"contains_alcohol": False},
            )
        )
        session.add(
            ProductReview(
                product_id="p_test",
                nickname="阿凯",
                rating=2,
                content="刺痛敏感肌不推荐",
                sentiment=-0.7,
                aspects=["敏感肌", "刺激"],
            )
        )
        await session.commit()

    async with test_session_factory() as session:
        rows = (await session.execute(select(ProductReview))).scalars().all()
        assert len(rows) == 1
        r = rows[0]
        assert r.sentiment == -0.7
        assert r.aspects == ["敏感肌", "刺激"]


async def test_review_sentiment_check_constraint(test_session_factory) -> None:
    """sentiment 超出 [-1, 1] 被 CHECK 约束拒绝(Postgres)。"""
    from sqlalchemy.exc import IntegrityError

    from server.storage.models import Product, ProductReview

    async with test_session_factory() as session:
        session.add(
            Product(
                product_id="p_x",
                title="T",
                brand="B",
                category="C",
                sub_category="S",
                base_price=10.0,
                in_stock=True,
                is_active=True,
                properties={},
            )
        )
        session.add(
            ProductReview(
                product_id="p_x",
                nickname="N",
                rating=3,
                content="超长评论 " * 5,
                sentiment=2.5,  # 越界 → CHECK 应该拒绝
                aspects=[],
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


# ──────────────────────────────────────────────────────────────
# JSONB properties 列 — 类目特化属性进这里
# ──────────────────────────────────────────────────────────────
async def test_product_properties_jsonb_roundtrip(test_session_factory) -> None:
    """Product.properties JSONB 写入混合类型(list/bool/str)并读回一致。"""
    from sqlalchemy import select

    from server.storage.models import Product

    payload = {
        "suitable_skin": ["敏感肌", "干皮"],
        "not_suitable_skin": [],
        "effects": ["保湿", "抗老"],
        "scene": ["夜用"],
        "contains_alcohol": False,
        "contains_fragrance": False,
        "age_group": "25+",
    }
    async with test_session_factory() as session:
        session.add(
            Product(
                product_id="p_jsonb",
                title="测试", brand="X", category="美妆护肤", sub_category="精华",
                base_price=99.0, in_stock=True, is_active=True,
                properties=payload,
            )
        )
        await session.commit()

    async with test_session_factory() as session:
        p = (
            await session.execute(
                select(Product).where(Product.product_id == "p_jsonb")
            )
        ).scalar_one()
        assert p.properties == payload
        assert p.properties["suitable_skin"] == ["敏感肌", "干皮"]
        assert p.properties["contains_alcohol"] is False


async def test_product_properties_jsonb_contains_query(test_session_factory) -> None:
    """Postgres @> 操作符:数组 contains 查询(GIN 索引加速)。"""
    from sqlalchemy import text

    from server.storage.models import Product

    async with test_session_factory() as session:
        session.add_all([
            Product(
                product_id=f"p_{i}", title=t, brand="X",
                category="美妆护肤", sub_category="精华",
                base_price=100.0, in_stock=True, is_active=True,
                properties=props,
            )
            for i, (t, props) in enumerate([
                ("敏感肌精华", {"suitable_skin": ["敏感肌"]}),
                ("油皮精华", {"suitable_skin": ["油皮"]}),
                ("通用精华", {"suitable_skin": ["敏感肌", "干皮"]}),
            ])
        ])
        await session.commit()

    async with test_session_factory() as session:
        # 找 suitable_skin 含 "敏感肌" 的(用 @> 操作符)
        result = await session.execute(
            text("SELECT product_id FROM products "
                 "WHERE properties @> :q::jsonb ORDER BY product_id"),
            {"q": '{"suitable_skin": ["敏感肌"]}'},
        )
        ids = [row[0] for row in result.all()]
        assert ids == ["p_0", "p_2"]  # 包含敏感肌的 2 个


# ──────────────────────────────────────────────────────────────
# Config:新增的 chunk2 配置项都能读到
# ──────────────────────────────────────────────────────────────
def test_new_chunk2_config_constants_present() -> None:
    from server import config

    assert config.REVIEW_MIN_LENGTH > 0
    assert 0.0 < config.REVIEW_DUP_CHAR_RATIO_MAX <= 1.0
    assert config.EMBEDDING_BATCH_SIZE > 0
    assert config.CAVEATS_TEXT_MAX_CHARS > 0
    assert 0.0 < config.CAVEATS_REVIEW_CHANGE_RATIO < 1.0
