"""
Ingest Manifest Repository(对应 docs/design.md §4.4.3 + §4.5)。

作用:增量 ingest 的"账本"。
- ingest.py 跑一遍前 → 拉全部 manifest → 跟 dataset JSON 算 hash 比 → 决定 upsert 哪些
- chunk_count 用于审计 Qdrant 里的 chunk 数,反向校验
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.storage.models import IngestManifest


class ManifestRepo:
    """ingest_manifest 表 CRUD — 不带 user_id(共享 catalog 元数据)。"""

    @staticmethod
    async def get(
        session: AsyncSession, product_id: str
    ) -> IngestManifest | None:
        result = await session.execute(
            select(IngestManifest).where(IngestManifest.product_id == product_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(session: AsyncSession) -> list[IngestManifest]:
        result = await session.execute(select(IngestManifest))
        return list(result.scalars().all())

    @staticmethod
    async def upsert(
        session: AsyncSession,
        product_id: str,
        content_hash: str,
        chunk_count: int,
    ) -> None:
        manifest = IngestManifest(
            product_id=product_id,
            content_hash=content_hash,
            chunk_count=chunk_count,
        )
        await session.merge(manifest)

    @staticmethod
    async def delete(session: AsyncSession, product_id: str) -> bool:
        result = await session.execute(
            delete(IngestManifest).where(IngestManifest.product_id == product_id)
        )
        return result.rowcount > 0

    @staticmethod
    async def as_hash_map(session: AsyncSession) -> dict[str, str]:
        """快查:product_id -> content_hash,用于 ingest diff。"""
        result = await session.execute(
            select(IngestManifest.product_id, IngestManifest.content_hash)
        )
        return {row[0]: row[1] for row in result.all()}


__all__ = ["ManifestRepo"]
