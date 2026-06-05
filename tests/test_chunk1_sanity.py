"""
Chunk 1 Sanity Test:
- 所有目标模块 import 成功
- DB create_all() 不抛错(所有表全部建出)
- seed_users 运行 + UserRepo 读回 3 个 user
- VectorIndex.ensure_collection() 创建 collection 并 count_points() 可读
- Cart 用户隔离(防越权)

依赖:跑前 `docker-compose up -d postgres` 启 Postgres 测试库。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

# 给"直接 python 跑"也能用
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ──────────────────────────────────────────────────────────────
# import sanity:任何 ImportError 立刻挂(不依赖 DB)
# ──────────────────────────────────────────────────────────────
def test_import_all_modules() -> None:
    import server  # noqa: F401
    import server.config  # noqa: F401
    import server.main  # noqa: F401
    import server.storage.catalog_repo  # noqa: F401
    import server.storage.db  # noqa: F401
    import server.storage.manifest_repo  # noqa: F401
    import server.storage.models  # noqa: F401
    import server.storage.user_repo  # noqa: F401
    import server.storage.vector_index  # noqa: F401
    import scripts.seed_users  # noqa: F401


def test_config_paths_resolve() -> None:
    from server import config

    assert config.QDRANT_PATH_ABS.is_absolute()
    assert config.INGEST_DATASET_DIR_ABS.is_absolute()
    assert "sample" in str(config.INGEST_DATASET_DIR_ABS).lower() or \
           "ecommerce_agent_dataset" in str(config.INGEST_DATASET_DIR_ABS).lower()


def test_config_database_urls() -> None:
    """Postgres URL 配齐 + dev / test 库隔离。"""
    from server import config

    assert config.DATABASE_URL.startswith("postgresql+asyncpg://")
    assert config.TEST_DATABASE_URL.startswith("postgresql+asyncpg://")
    # 测试库跟 dev 库不能一样,否则测试会污染 dev 数据
    assert config.DATABASE_URL != config.TEST_DATABASE_URL


def test_models_count() -> None:
    """11 张表全部注册到 metadata。"""
    from server.storage.db import Base
    import server.storage.models  # noqa: F401

    table_names = set(Base.metadata.tables.keys())
    expected = {
        # D1
        "products", "skus", "product_faqs",
        "product_reviews", "product_review_summary",
        # D2
        "users", "user_profile", "cart_items", "orders", "chat_history",
        # D3
        "ingest_manifest",
    }
    missing = expected - table_names
    assert not missing, f"missing tables: {missing}"
    assert len(table_names) >= 11


# ──────────────────────────────────────────────────────────────
# DB 集成测试:需要 Postgres 容器 in running
# ──────────────────────────────────────────────────────────────
async def test_create_all_and_seed(
    test_session_factory: async_sessionmaker,
) -> None:
    """schema 建出 + 3 个 user seed 进去。"""
    from server.storage.user_repo import UserRepo
    from scripts.seed_users import DEMO_USERS

    async with test_session_factory() as session:
        for entry in DEMO_USERS:
            await UserRepo.upsert_user(session, entry["user_id"], entry["display_name"])
            await session.flush()
            await UserRepo.upsert_profile(session, entry["user_id"], **entry["profile"])
        await session.commit()

    # 读回 3 个 user
    async with test_session_factory() as session:
        users = await UserRepo.list_users(session)
        assert len(users) == 3
        display_names = {u.display_name for u in users}
        assert display_names == {"Alice", "Bob", "Charlie"}

        # 抽一个 profile 验证 preferences JSON 落盘正确
        alice_profile = await UserRepo.get_profile(session, "demo_user_1")
        assert alice_profile is not None
        assert alice_profile.preferences.get("skin_type") == "敏感肌"


async def test_cart_user_isolation(
    test_session_factory: async_sessionmaker,
) -> None:
    """加 cart 走 user_id,跨用户隔离不串数据。"""
    from server.storage.models import Product, SKU
    from server.storage.user_repo import UserRepo

    # 准备 2 个 user + 1 个 product/sku(cart 外键依赖)
    async with test_session_factory() as session:
        await UserRepo.upsert_user(session, "u_a", "UserA")
        await UserRepo.upsert_user(session, "u_b", "UserB")
        await session.flush()
        session.add(Product(
            product_id="p_x", title="x", brand="b", category="c",
            sub_category="s", base_price=10.0,
            in_stock=True, is_active=True,
            properties={},
        ))
        session.add(SKU(sku_id="s_x_1", product_id="p_x", properties={}, price=10.0))
        await session.commit()

    # UserA 加购,UserB 看不到
    async with test_session_factory() as session:
        await UserRepo.add_to_cart(session, "u_a", "s_x_1", 2)
        await session.commit()

    async with test_session_factory() as session:
        a_cart = await UserRepo.list_cart(session, "u_a")
        b_cart = await UserRepo.list_cart(session, "u_b")
        assert len(a_cart) == 1
        assert a_cart[0].qty == 2
        assert len(b_cart) == 0


# ──────────────────────────────────────────────────────────────
# Qdrant 嵌入式 collection — 跟 Postgres 无关,临时目录即可
# ──────────────────────────────────────────────────────────────
async def test_qdrant_collection_init(tmp_path: Path) -> None:
    from server import config
    from server.storage import vector_index as vi_module

    tmp_qdrant = tmp_path / "qdrant"
    tmp_qdrant.mkdir()

    test_index = vi_module.VectorIndex(
        path=str(tmp_qdrant),
        collection_name="test_collection",
        dimension=config.EMBEDDING_DIMENSION,
    )
    try:
        await test_index.ensure_collection()
        n = await test_index.count_points()
        assert n == 0
    finally:
        await test_index.close()
        shutil.rmtree(tmp_qdrant, ignore_errors=True)


if __name__ == "__main__":
    # 直接 python 跑:只跑纯逻辑测试,DB / Qdrant 需要 pytest fixture
    test_import_all_modules()
    test_config_paths_resolve()
    test_config_database_urls()
    test_models_count()
    print("[sanity] pure-logic tests passed. DB tests require pytest + docker-compose.")
