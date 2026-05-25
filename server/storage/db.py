"""
SQLAlchemy v2 async engine + Session 工厂(对应 docs/design.md §4.4)。

- 后端 SQLite,driver aiosqlite,全程 async
- SQLite 需手动 PRAGMA foreign_keys=ON 才生效外键约束
- DeclarativeBase 给 models.py 共享
- get_session() 是 FastAPI Depends 用的 async generator
- create_all() 给 lifespan 调用,首跑创建 12 张表
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy import event
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


def _ensure_parent_dir(sqlite_path: Path) -> None:
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)


def _build_async_url(sqlite_path: Path) -> str:
    # SQLAlchemy aiosqlite URL 形式:sqlite+aiosqlite:///绝对路径
    return f"sqlite+aiosqlite:///{sqlite_path.as_posix()}"


_ensure_parent_dir(config.SQLITE_PATH_ABS)

engine: AsyncEngine = create_async_engine(
    _build_async_url(config.SQLITE_PATH_ABS),
    echo=False,
    future=True,
)


@event.listens_for(engine.sync_engine, "connect")
def _enable_sqlite_fk(dbapi_conn, _conn_record) -> None:  # type: ignore[no-untyped-def]
    """SQLite 默认不强制 FK,这里每个新连接开启 PRAGMA foreign_keys=ON。"""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


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
