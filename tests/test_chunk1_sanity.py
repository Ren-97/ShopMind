"""
Chunk 1 Sanity Test —
- 所有目标模块 import 成功
- DB create_all() 不抛错(12 张表全部建出)
- seed_users 运行 + UserRepo 读回 3 个 user
- VectorIndex.ensure_collection() 创建 collection 并 count_points() 可读
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

import pytest

# 给"直接 python 跑"也能用
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ──────────────────────────────────────────────────────────────
# import sanity:任何 ImportError 立刻挂
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

    assert config.SQLITE_PATH_ABS.is_absolute()
    assert config.QDRANT_PATH_ABS.is_absolute()
    assert config.INGEST_DATASET_DIR_ABS.is_absolute()
    # 默认应指向 sample(可被 .env 覆盖,这里只断言 absolute)
    assert "sample" in str(config.INGEST_DATASET_DIR_ABS).lower() or \
           "ecommerce_agent_dataset" in str(config.INGEST_DATASET_DIR_ABS).lower()


def test_models_count() -> None:
    """12 张表全部注册到 metadata。"""
    from server.storage.db import Base
    import server.storage.models  # noqa: F401  触发注册

    table_names = set(Base.metadata.tables.keys())
    expected = {
        # D1
        "products", "skus", "product_attributes", "product_faqs",
        "product_reviews", "product_caveats",
        # D2
        "users", "user_profile", "cart_items", "orders", "chat_history",
        # D3
        "ingest_manifest",
    }
    missing = expected - table_names
    assert not missing, f"missing tables: {missing}"
    assert len(table_names) >= 12


# ──────────────────────────────────────────────────────────────
# 用临时 SQLite + 临时 Qdrant 跑端到端
# ──────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_create_all_and_seed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """临时 DB + 临时 Qdrant,验证表建出 + 3 个 user seed 进去。"""
    # 用临时目录覆盖 config(import 前 patch 不容易,这里 patch 模块属性)
    tmp_db = tmp_path / "test_shopmind.db"
    tmp_qdrant = tmp_path / "qdrant"
    tmp_qdrant.mkdir()

    from server import config
    from server.storage import db as db_module
    from server.storage import vector_index as vi_module
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    # 替换为临时 engine
    test_engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_db.as_posix()}", future=True
    )
    test_session = async_sessionmaker(
        bind=test_engine, expire_on_commit=False, autoflush=False
    )
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "AsyncSessionLocal", test_session)

    # create_all
    async with test_engine.begin() as conn:
        await conn.run_sync(db_module.Base.metadata.create_all)

    # seed
    from server.storage.user_repo import UserRepo
    from scripts.seed_users import DEMO_USERS

    async with test_session() as session:
        for entry in DEMO_USERS:
            await UserRepo.upsert_user(session, entry["user_id"], entry["display_name"])
            await session.flush()
            await UserRepo.upsert_profile(session, entry["user_id"], **entry["profile"])
        await session.commit()

    # 读回 3 个 user
    async with test_session() as session:
        users = await UserRepo.list_users(session)
        assert len(users) == 3
        display_names = {u.display_name for u in users}
        assert display_names == {"Alice", "Bob", "Charlie"}

        # 抽一个 profile 验证 preferences JSON 落盘正确
        alice_profile = await UserRepo.get_profile(session, "demo_user_1")
        assert alice_profile is not None
        assert alice_profile.preferences.get("skin_type") == "敏感肌"

    await test_engine.dispose()

    # Qdrant ensure_collection — 用临时目录
    test_index = vi_module.VectorIndex(
        path=str(tmp_qdrant),
        collection_name="test_collection",
        dimension=config.EMBEDDING_DIMENSION,
    )
    await test_index.ensure_collection()
    n = await test_index.count_points()
    assert n == 0
    await test_index.close()


@pytest.mark.asyncio
async def test_cart_user_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """加 cart 走 user_id,跨用户隔离不串数据。"""
    tmp_db = tmp_path / "iso.db"
    from server.storage import db as db_module
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    test_engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_db.as_posix()}", future=True
    )
    test_session = async_sessionmaker(
        bind=test_engine, expire_on_commit=False, autoflush=False
    )
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "AsyncSessionLocal", test_session)

    async with test_engine.begin() as conn:
        await conn.run_sync(db_module.Base.metadata.create_all)

    from server.storage.models import Product, SKU
    from server.storage.user_repo import UserRepo

    # 准备 2 个 user + 1 个 product/sku(cart 外键依赖)
    async with test_session() as session:
        await UserRepo.upsert_user(session, "u_a", "UserA")
        await UserRepo.upsert_user(session, "u_b", "UserB")
        await session.flush()
        session.add(Product(
            product_id="p_x", title="x", brand="b", category="c",
            sub_category="s", base_price=10.0,
            contains_alcohol=False, contains_fragrance=False,
            in_stock=True, is_active=True,
        ))
        session.add(SKU(sku_id="s_x_1", product_id="p_x", properties={}, price=10.0))
        await session.commit()

    # UserA 加购,UserB 看不到
    async with test_session() as session:
        await UserRepo.add_to_cart(session, "u_a", "s_x_1", 2)
        await session.commit()

    async with test_session() as session:
        a_cart = await UserRepo.list_cart(session, "u_a")
        b_cart = await UserRepo.list_cart(session, "u_b")
        assert len(a_cart) == 1
        assert a_cart[0].qty == 2
        assert len(b_cart) == 0

    await test_engine.dispose()


if __name__ == "__main__":
    # 也可以直接 python 跑(脱离 pytest)
    test_import_all_modules()
    test_config_paths_resolve()
    test_models_count()
    print("[sanity] sync tests passed.")

    tmp = Path("./_sanity_tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir()

    class _MonkeyPatch:
        def __init__(self) -> None:
            self._undo: list = []

        def setattr(self, target, name, value):  # type: ignore[no-untyped-def]
            old = getattr(target, name)
            self._undo.append((target, name, old))
            setattr(target, name, value)

        def undo_all(self) -> None:
            for target, name, old in reversed(self._undo):
                setattr(target, name, old)

    mp = _MonkeyPatch()
    try:
        asyncio.run(test_create_all_and_seed(tmp, mp))  # type: ignore[arg-type]
        print("[sanity] create_all + seed_users passed.")
        mp.undo_all()
        mp = _MonkeyPatch()
        asyncio.run(test_cart_user_isolation(tmp, mp))  # type: ignore[arg-type]
        print("[sanity] cart user isolation passed.")
    finally:
        mp.undo_all()
        shutil.rmtree(tmp, ignore_errors=True)
    print("[sanity] ALL OK")
