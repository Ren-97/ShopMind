"""Shared test fixtures for ShopMind tests.

策略:
- 跑测试前需要 `docker-compose up -d postgres`(否则 Postgres 不可达 → skip)
- test fixture 连 TEST_DATABASE_URL(`shopmind_test` 库,跟 dev `shopmind` 库隔离)
- 每个测试 drop_all + create_all 保证干净

纯逻辑测试(chunker / hash / diff)不需要 DB,不依赖 fixture。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from server import config
from server.storage.db import Base


async def _can_connect(url: str) -> bool:
    """探测 TEST_DATABASE_URL 是否可达;不可达时跳过依赖的 DB 测试。"""
    engine = create_async_engine(url, future=True)
    try:
        async with engine.connect():
            pass
        return True
    except Exception:
        return False
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def test_engine() -> AsyncIterator[AsyncEngine]:
    """干净的 Postgres test 引擎 — 每个测试 drop + create 一遍 schema。"""
    import pytest

    if not await _can_connect(config.TEST_DATABASE_URL):
        pytest.skip(
            "Postgres 测试库不可达 — 先跑 `docker-compose up -d postgres` 再跑测试"
        )

    engine = create_async_engine(config.TEST_DATABASE_URL, future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def test_session_factory(
    test_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    """async sessionmaker bound to test_engine。"""
    return async_sessionmaker(
        bind=test_engine, expire_on_commit=False, autoflush=False
    )
