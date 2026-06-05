"""
Reset demo 用户数据 — 测试 / 答辩前一键回到干净状态。

清掉(仅 demo_user_*,**不动 eval_user_***):
  - chat_history  全部对话历史
  - cart_items    购物车
  - orders        订单历史
  - user_profile  重 seed 回 scripts/seed_users.py 里定义的初始值
                  (覆盖你 demo 期间通过 update_preference / 编辑地址 改过的字段)

整个删除(demo 期间通过 ➕ 新建的 user_* 用户):
  - 连同其 chat_history / cart_items / orders / user_profile / users 行一并删除,
    回到"只剩 demo_user_* + eval_user_*"的最初状态

**不动**:
  - eval_user_*  数据(给 tests/eval_runner.py 用,跟 demo 隔离)
  - users 主表   demo_user_* 行(display_name 不变,user_id → 人名 映射保留)
  - products / skus / reviews / caveats / 等 catalog 数据(那是 ingest 产出)
  - Qdrant 向量索引

跑法:
  python scripts/reset_demo.py

要求:Postgres 起着(docker-compose up -d postgres)。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 允许直接 `python scripts/reset_demo.py` 运行(把项目根加入 sys.path)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from sqlalchemy import delete  # noqa: E402

from server.storage.db import AsyncSessionLocal  # noqa: E402
from server.storage.models import (  # noqa: E402
    CartItem,
    ChatHistory,
    Order,
    User,
    UserProfile,
)
from server.storage.user_repo import UserRepo  # noqa: E402

from scripts.seed_users import DEMO_USERS  # noqa: E402


DEMO_USER_IDS: list[str] = [u["user_id"] for u in DEMO_USERS]


async def reset_demo() -> None:
    # 0. 整删 demo 期间新建的 user_* 用户(数据 + users 行 → 从下拉消失)
    #    先删子表行再删 users 行,不依赖 FK cascade。
    async with AsyncSessionLocal() as session:
        all_users = await UserRepo.list_users(session)
        created_ids = [u.user_id for u in all_users if u.user_id.startswith("user_")]
        if created_ids:
            await session.execute(
                delete(ChatHistory).where(ChatHistory.user_id.in_(created_ids))
            )
            await session.execute(
                delete(CartItem).where(CartItem.user_id.in_(created_ids))
            )
            await session.execute(delete(Order).where(Order.user_id.in_(created_ids)))
            await session.execute(
                delete(UserProfile).where(UserProfile.user_id.in_(created_ids))
            )
            await session.execute(delete(User).where(User.user_id.in_(created_ids)))
        await session.commit()

    # 1. 清掉 demo_user_* 业务数据
    async with AsyncSessionLocal() as session:
        chat_res = await session.execute(
            delete(ChatHistory).where(ChatHistory.user_id.in_(DEMO_USER_IDS))
        )
        cart_res = await session.execute(
            delete(CartItem).where(CartItem.user_id.in_(DEMO_USER_IDS))
        )
        order_res = await session.execute(
            delete(Order).where(Order.user_id.in_(DEMO_USER_IDS))
        )
        await session.commit()

    chat_n = chat_res.rowcount or 0
    cart_n = cart_res.rowcount or 0
    order_n = order_res.rowcount or 0

    # 2. 重 seed profile(把 demo 期间用户改过的字段全部覆盖回初始)
    async with AsyncSessionLocal() as session:
        for entry in DEMO_USERS:
            await UserRepo.upsert_profile(
                session, entry["user_id"], **entry["profile"]
            )
        await session.commit()

    # 3. 报告
    print("[reset_demo] OK — demo 数据已重置")
    print(f"  · 新建用户      整删 {len(created_ids)} 个 user_*(连同其全部数据)")
    print(f"  · chat_history  删 {chat_n} 行(demo_user_*)")
    print(f"  · cart_items    删 {cart_n} 行(demo_user_*)")
    print(f"  · orders        删 {order_n} 行(demo_user_*)")
    print(f"  · user_profile  重 seed {len(DEMO_USERS)} 个 demo user 回初始")
    print(f"\n  eval_user_* 不动,Postgres / Qdrant catalog 不动。")


def main() -> None:
    asyncio.run(reset_demo())


if __name__ == "__main__":
    main()
