"""SQLAlchemy v2 async engine + Session 工厂(对应 docs/design.md §4.4)。

- 后端:**Postgres + asyncpg**(`docker-compose up -d` 起本地实例)
- DeclarativeBase 给 models.py 共享
- get_session() 是 FastAPI Depends 用的 async generator
- create_all() 给 lifespan 调用,首跑创建所有表 + GIN 索引
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from server import config


class Base(DeclarativeBase):
    """所有 ORM 模型共享的声明基类。"""


engine: AsyncEngine = create_async_engine(
    config.DATABASE_URL,
    echo=False,
    future=True,
    pool_pre_ping=True,  # 容器重启 / 长时间空闲后自动重连
)


AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
    class_=AsyncSession,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI Depends 用的 session 依赖,自动 close。"""
    async with AsyncSessionLocal() as session:
        yield session


async def create_all() -> None:
    """启动期建表(幂等)。"""
    from server.storage import models  # noqa: F401  确保 metadata 被注册

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose() -> None:
    """关闭连接池,lifespan 退出调。"""
    await engine.dispose()


__all__ = [
    "Base",
    "engine",
    "AsyncSessionLocal",
    "get_session",
    "create_all",
    "dispose",
]
