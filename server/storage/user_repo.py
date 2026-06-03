"""
User Repository(对应 docs/design.md §4.4.2 + §4.6.8)。

设计原则:
- **所有方法第一参数 `user_id: str`**(强制注入,Repo 层不接受不带 user_id 的查询)
- **所有 SQL 永远带 WHERE user_id=?**(跨用户隔离,防越权)
- 覆盖 5 张用户态表:users / user_profile / cart_items / orders / chat_history
- Chunk 1 提供骨架方法,Chunk 4/5 Tool 实现时按需扩展
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.storage.models import (
    CartItem,
    ChatHistory,
    Order,
    User,
    UserProfile,
)


class UserRepo:
    """用户态 Repo — 全部方法 `user_id` 必填。"""

    # ─── users ────────────────────────────────────
    @staticmethod
    async def get_user(session: AsyncSession, user_id: str) -> User | None:
        result = await session.execute(
            select(User).where(User.user_id == user_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_users(session: AsyncSession) -> list[User]:
        """顶栏切换用户用 — 唯一无 user_id 过滤的方法(只读全表)。"""
        result = await session.execute(select(User).order_by(User.user_id))
        return list(result.scalars().all())

    @staticmethod
    async def upsert_user(
        session: AsyncSession, user_id: str, display_name: str
    ) -> None:
        await session.merge(User(user_id=user_id, display_name=display_name))

    # ─── user_profile ─────────────────────────────
    @staticmethod
    async def get_profile(
        session: AsyncSession, user_id: str
    ) -> UserProfile | None:
        result = await session.execute(
            select(UserProfile).where(UserProfile.user_id == user_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def upsert_profile(
        session: AsyncSession,
        user_id: str,
        *,
        age: int | None = None,
        gender: str | None = None,
        height_cm: float | None = None,
        weight_kg: float | None = None,
        consumption_tier: str | None = None,
        recipient_name: str | None = None,
        phone: str | None = None,
        address: str | None = None,
        preferences: dict[str, Any] | None = None,
    ) -> None:
        profile = UserProfile(
            user_id=user_id,
            age=age,
            gender=gender,
            height_cm=height_cm,
            weight_kg=weight_kg,
            consumption_tier=consumption_tier,
            recipient_name=recipient_name,
            phone=phone,
            address=address,
            preferences=preferences if preferences is not None else {},
        )
        await session.merge(profile)

    # ─── cart_items ───────────────────────────────
    @staticmethod
    async def list_cart(session: AsyncSession, user_id: str) -> list[CartItem]:
        result = await session.execute(
            select(CartItem)
            .where(CartItem.user_id == user_id)
            .order_by(CartItem.added_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def add_to_cart(
        session: AsyncSession, user_id: str, sku_id: str, qty: int
    ) -> CartItem:
        """加购:若 (user_id, sku_id) 已存在 → qty 累加;否则插入新行。"""
        existing = await session.execute(
            select(CartItem).where(
                CartItem.user_id == user_id, CartItem.sku_id == sku_id
            )
        )
        item = existing.scalar_one_or_none()
        if item is not None:
            item.qty += qty
        else:
            item = CartItem(user_id=user_id, sku_id=sku_id, qty=qty)
            session.add(item)
        return item

    @staticmethod
    async def update_cart_qty(
        session: AsyncSession, user_id: str, sku_id: str, qty: int
    ) -> CartItem | None:
        existing = await session.execute(
            select(CartItem).where(
                CartItem.user_id == user_id, CartItem.sku_id == sku_id
            )
        )
        item = existing.scalar_one_or_none()
        if item is None:
            return None
        item.qty = qty
        return item

    @staticmethod
    async def remove_from_cart(
        session: AsyncSession, user_id: str, sku_id: str
    ) -> bool:
        result = await session.execute(
            delete(CartItem).where(
                CartItem.user_id == user_id, CartItem.sku_id == sku_id
            )
        )
        return result.rowcount > 0

    @staticmethod
    async def clear_cart(session: AsyncSession, user_id: str) -> None:
        await session.execute(delete(CartItem).where(CartItem.user_id == user_id))

    # ─── orders ───────────────────────────────────
    @staticmethod
    async def create_order(
        session: AsyncSession,
        user_id: str,
        order_id: str,
        items: list[dict[str, Any]],
        address: str,
        total_price: float,
        *,
        recipient_name: str | None = None,
        phone: str | None = None,
        status: str = "pending",
    ) -> Order:
        """创建订单 — 收货三件套(address / recipient_name / phone)都做快照存进 Order。

        调用方在下单时一次性从 user_profile 取这三个字段,后续 profile 改动
        不影响历史订单(§4.4.2 业务行为)。
        """
        order = Order(
            order_id=order_id,
            user_id=user_id,
            status=status,
            items=items,
            address=address,
            recipient_name=recipient_name,
            phone=phone,
            total_price=total_price,
        )
        session.add(order)
        return order

    @staticmethod
    async def get_order(
        session: AsyncSession, user_id: str, order_id: str
    ) -> Order | None:
        result = await session.execute(
            select(Order).where(
                Order.user_id == user_id, Order.order_id == order_id
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_orders(
        session: AsyncSession, user_id: str, *, limit: int = 50
    ) -> list[Order]:
        result = await session.execute(
            select(Order)
            .where(Order.user_id == user_id)
            .order_by(desc(Order.created_at))
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def update_order_status(
        session: AsyncSession, user_id: str, order_id: str, status: str
    ) -> bool:
        order = await UserRepo.get_order(session, user_id, order_id)
        if order is None:
            return False
        order.status = status
        return True

    # ─── chat_history ─────────────────────────────
    @staticmethod
    async def append_message(
        session: AsyncSession,
        user_id: str,
        session_id: str,
        role: str,
        content: str,
        tool_calls: list[dict[str, Any]] | None = None,
        card_refs: dict[str, Any] | None = None,
    ) -> ChatHistory:
        msg = ChatHistory(
            user_id=user_id,
            session_id=session_id,
            role=role,
            content=content,
            tool_calls=tool_calls,
            card_refs=card_refs,
        )
        session.add(msg)
        return msg

    @staticmethod
    async def clear_chat_history(
        session: AsyncSession,
        user_id: str,
        session_id: str | None = None,
    ) -> int:
        """删 chat_history 行。指定 session_id 只清该 session;不指定清这个 user 的所有。

        客户端 🔄 清空对话按钮调本方法 → 真删 DB(不可恢复,跟 ChatGPT / 微信清空一致)。
        """
        stmt = delete(ChatHistory).where(ChatHistory.user_id == user_id)
        if session_id is not None:
            stmt = stmt.where(ChatHistory.session_id == session_id)
        result = await session.execute(stmt)
        return result.rowcount or 0

    @staticmethod
    async def list_messages(
        session: AsyncSession,
        user_id: str,
        session_id: str,
        *,
        limit: int | None = None,
    ) -> list[ChatHistory]:
        stmt = (
            select(ChatHistory)
            .where(
                ChatHistory.user_id == user_id,
                ChatHistory.session_id == session_id,
            )
            .order_by(ChatHistory.created_at, ChatHistory.msg_id)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def list_recent_turns(
        session: AsyncSession,
        user_id: str,
        session_id: str,
        n_turns: int,
    ) -> list[ChatHistory]:
        """取最近 N 条 message(Planner 看上下文用,§4.2)。"""
        stmt = (
            select(ChatHistory)
            .where(
                ChatHistory.user_id == user_id,
                ChatHistory.session_id == session_id,
            )
            .order_by(desc(ChatHistory.created_at), desc(ChatHistory.msg_id))
            .limit(n_turns)
        )
        result = await session.execute(stmt)
        msgs = list(result.scalars().all())
        msgs.reverse()  # 时间正序还回去
        return msgs

    @staticmethod
    async def search_messages(
        session: AsyncSession,
        user_id: str,
        query: str,
        *,
        top_n: int = 5,
    ) -> list[ChatHistory]:
        """recall_history 工具用:跨 session 搜本用户历史消息(§4.6.1 兜底)。

        V1:`ILIKE %query%` + 按时间倒排(近的优先)。语义检索(embed chat_history)
        留给 V2 — 当前 chat_history 量小,关键词召回足够。
        """
        if not query or not query.strip():
            return []
        pattern = f"%{query.strip()}%"
        stmt = (
            select(ChatHistory)
            .where(
                ChatHistory.user_id == user_id,
                ChatHistory.role.in_(("user", "assistant")),
                ChatHistory.content.ilike(pattern),
            )
            .order_by(desc(ChatHistory.created_at), desc(ChatHistory.msg_id))
            .limit(top_n)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())


__all__ = ["UserRepo"]
