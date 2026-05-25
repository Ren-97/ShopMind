"""
Seed 3 个 demo 用户(对应 docs/design.md §4.4.2 + §4.6.8 多用户演示)。

策略:
- 选 3 个**画像鲜明 + 跨类目分散**的 demo,覆盖 dataset/sample/ 的 4 个类目
  ① demo_user_1 / Alice    — 27 岁女,美妆护肤(敏感肌),中等消费
  ② demo_user_2 / Bob      — 32 岁男,数码电子(iOS / 摄影),高消费
  ③ demo_user_3 / Charlie  — 24 岁,服饰运动 + 食品生活(素食 / 简约),节约型
- preferences JSON 各自带类目特化字段,验证 §4.4.2 灵活扩展
- 跑法:`python -m scripts.seed_users` 或 `python scripts/seed_users.py`
- 幂等:已存在则 merge 更新

注意:V1 无 auth,前端顶栏下拉就是这 3 个 display_name。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

# 允许直接 `python scripts/seed_users.py` 运行(把项目根加入 sys.path)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from server.storage.db import AsyncSessionLocal, create_all  # noqa: E402
from server.storage.user_repo import UserRepo  # noqa: E402


DEMO_USERS: list[dict[str, Any]] = [
    {
        "user_id": "demo_user_1",
        "display_name": "Alice",
        "profile": {
            "age": 27,
            "gender": "female",
            "height_cm": 165.0,
            "weight_kg": 52.0,
            "consumption_tier": "中等",
            "recipient_name": "Alice Wang",
            "phone": "13800000001",
            "address": "上海市浦东新区世纪大道 100 号 A 座 1502",
            "preferences": {
                # 美妆类目特化
                "skin_type": "敏感肌",
                "skin_concerns": ["保湿", "舒缓"],
                "fragrance_pref": "无香",
                # 通用
                "brand_prefer": ["雅诗兰黛"],
                "brand_exclude": [],
            },
        },
    },
    {
        "user_id": "demo_user_2",
        "display_name": "Bob",
        "profile": {
            "age": 32,
            "gender": "male",
            "height_cm": 178.0,
            "weight_kg": 72.0,
            "consumption_tier": "高消费",
            "recipient_name": "Bob Chen",
            "phone": "13800000002",
            "address": "北京市海淀区中关村大街 27 号 1801",
            "preferences": {
                # 数码类目特化
                "usage": ["办公", "摄影", "出差"],
                "os_pref": "iOS",
                # 通用
                "brand_prefer": ["Apple", "Sony"],
                "brand_exclude": [],
            },
        },
    },
    {
        "user_id": "demo_user_3",
        "display_name": "Charlie",
        "profile": {
            "age": 24,
            "gender": "other",
            "height_cm": 170.0,
            "weight_kg": 58.0,
            "consumption_tier": "节约型",
            "recipient_name": "Charlie Liu",
            "phone": "13800000003",
            "address": "杭州市西湖区文三路 388 号 8 楼",
            "preferences": {
                # 服饰类目特化
                "clothing_size": "M",
                "shoe_size": 41,
                "style_pref": ["简约", "运动"],
                # 食品类目特化
                "dietary_restrictions": ["素食", "低糖"],
                # 通用
                "brand_prefer": [],
                "brand_exclude": [],
            },
        },
    },
]


async def seed_users() -> None:
    """幂等 seed:3 个 demo user + profile,merge 模式不破坏已有数据。"""
    # 确保表已建(首跑 / DB reset 后)
    await create_all()

    async with AsyncSessionLocal() as session:
        for entry in DEMO_USERS:
            await UserRepo.upsert_user(
                session, entry["user_id"], entry["display_name"]
            )
            # FK 依赖 users 行先存在,flush 一下让 merge 落到 DB
            await session.flush()
            await UserRepo.upsert_profile(session, entry["user_id"], **entry["profile"])
        await session.commit()

    print(f"[seed_users] OK: seeded {len(DEMO_USERS)} demo users")
    for entry in DEMO_USERS:
        print(f"  - {entry['user_id']} ({entry['display_name']})")


def main() -> None:
    asyncio.run(seed_users())


if __name__ == "__main__":
    main()
