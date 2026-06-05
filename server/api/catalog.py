"""REST /catalog — 目录派生的只读元数据。

GET /catalog/facets:返回"在售有货"商品按类目聚合的 facet
(sub_categories / brands / 价位区间)。客户端用它生成 starter chip ——
chip 只从真实存在的词填模板,**构造上保证**点了有结果(库存感知建议)。

无 user_id:facet 是全局目录事实,不涉及用户态。前端可缓存。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from server.api.deps import get_session_factory
from server.storage.catalog_repo import CatalogRepo

router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("/facets")
async def get_facets(
    session_factory: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_session_factory)
    ],
) -> dict[str, Any]:
    async with session_factory() as session:
        categories = await CatalogRepo.get_facets(session)
    return {"categories": categories}


__all__ = ["router"]
