"""REST /profile — 用户档案读写(§4.4.2 + §4.6.8)。

V1 端点:
- GET   /profile  → 当前用户 profile(不存在 → 空对象,字段全 None)
- PATCH /profile  → 局部更新(任何字段可选;preferences 走合并)
- GET   /users    → 顶栏 user 切换下拉(返回所有 users 列表,**唯一不带 user_id 的查询**)
- POST  /users    → 新建空白用户(无鉴权,demo 用;生成 user_id,profile 留空)

profile 主要由 chat 的 update_preference tool 写入;此端点给 Android 客户端
做基础 CRUD(例如首次填地址、查看个人信息页等)。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from server.api.deps import get_current_user, get_session_factory
from server.storage.user_repo import UserRepo

router = APIRouter(tags=["profile"])


# ──────────────────────────────────────────────────────────────
# Models
# ──────────────────────────────────────────────────────────────
class ProfileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    age: int | None = None
    gender: str | None = None
    height_cm: float | None = None
    weight_kg: float | None = None
    consumption_tier: str | None = None
    recipient_name: str | None = None
    phone: str | None = None
    address: str | None = None
    preferences: dict[str, Any] = Field(default_factory=dict)


class ProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    age: int | None = Field(default=None, ge=0, le=150)
    gender: Literal["male", "female"] | None = None
    height_cm: float | None = Field(default=None, gt=0, lt=300)
    weight_kg: float | None = Field(default=None, gt=0, lt=500)
    consumption_tier: str | None = None
    recipient_name: str | None = None
    phone: str | None = None
    address: str | None = None
    preferences: dict[str, Any] | None = Field(
        default=None,
        description="局部合并:传入 key 覆盖现有同 key,其他 key 保留;值为 null 的 key 删除",
    )


class UserListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    display_name: str


class CreateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=50)


# ──────────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────────
@router.get("/profile", response_model=ProfileResponse)
async def get_profile(
    user_id: Annotated[str, Depends(get_current_user)],
    session_factory: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_session_factory)
    ],
) -> ProfileResponse:
    async with session_factory() as session:
        profile = await UserRepo.get_profile(session, user_id)
        if profile is None:
            return ProfileResponse(user_id=user_id)
        return ProfileResponse(
            user_id=profile.user_id,
            age=profile.age,
            gender=profile.gender,
            height_cm=profile.height_cm,
            weight_kg=profile.weight_kg,
            consumption_tier=profile.consumption_tier,
            recipient_name=profile.recipient_name,
            phone=profile.phone,
            address=profile.address,
            preferences=dict(profile.preferences or {}),
        )


@router.patch("/profile", response_model=ProfileResponse)
async def patch_profile(
    body: ProfilePatch,
    user_id: Annotated[str, Depends(get_current_user)],
    session_factory: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_session_factory)
    ],
) -> ProfileResponse:
    """局部更新:body 里未传的字段保留原值。

    preferences 逐 key 合并:值为 null 的 key 删除(pop),其余覆盖/新增。
    首类列(consumption_tier 等)显式传 null → SET NULL。
    """
    async with session_factory() as session:
        existing = await UserRepo.get_profile(session, user_id)
        # 现有值 → 起点
        merged: dict[str, Any] = {
            "age": existing.age if existing else None,
            "gender": existing.gender if existing else None,
            "height_cm": existing.height_cm if existing else None,
            "weight_kg": existing.weight_kg if existing else None,
            "consumption_tier": existing.consumption_tier if existing else None,
            "recipient_name": existing.recipient_name if existing else None,
            "phone": existing.phone if existing else None,
            "address": existing.address if existing else None,
            "preferences": dict(existing.preferences or {}) if existing else {},
        }
        patch_data = body.model_dump(exclude_unset=True)
        # preferences 逐 key 合并:null 值删除该 key,其余覆盖 / 新增
        if "preferences" in patch_data and patch_data["preferences"] is not None:
            for k, v in patch_data["preferences"].items():
                if v is None:
                    merged["preferences"].pop(k, None)
                else:
                    merged["preferences"][k] = v
            patch_data.pop("preferences")
        merged.update({k: v for k, v in patch_data.items()})

        await UserRepo.upsert_profile(session, user_id, **merged)
        await session.commit()

        return ProfileResponse(user_id=user_id, **merged)


@router.get("/users", response_model=list[UserListItem])
async def list_users(
    session_factory: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_session_factory)
    ],
) -> list[UserListItem]:
    """顶栏 user 切换用 — 唯一无 user_id 过滤的查询(§4.6.8 业务约定)。"""
    async with session_factory() as session:
        users = await UserRepo.list_users(session)
        return [
            UserListItem(user_id=u.user_id, display_name=u.display_name) for u in users
        ]


@router.post("/users", response_model=UserListItem, status_code=201)
async def create_user(
    body: CreateUserRequest,
    session_factory: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_session_factory)
    ],
) -> UserListItem:
    """新建空白用户(无鉴权,demo 用):生成 user_id,建 users 行,profile 留空。

    profile 不预建 — GET /profile 对无 profile 用户返回全 None 空对象,
    首次 PATCH /profile 或对话 update_preference 时再 upsert(§4.4.2)。
    """
    user_id = f"user_{uuid4().hex[:12]}"
    name = body.display_name.strip()
    async with session_factory() as session:
        await UserRepo.upsert_user(session, user_id, name)
        await session.commit()
    return UserListItem(user_id=user_id, display_name=name)


__all__ = ["router"]
