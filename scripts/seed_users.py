"""
Seed demo 用户 + eval 专用用户(对应 docs/design.md §4.4.2 + §4.6.8 + §4.10)。

两组用户,**完全隔离**:
- DEMO_USERS:Android 前端顶栏切换,演示多用户差异化推荐
  ① demo_user_1 / Alice    — 27 岁女,美妆护肤(敏感肌),中等消费
  ② demo_user_2 / Bob      — 32 岁男,数码电子(iOS / 摄影),高消费
  ③ demo_user_3 / Charlie  — 24 岁男,服饰运动 + 食品饮料(素食 / 简约),节约型
- EVAL_USERS:`tests/eval.py` 跑批专用,**不暴露给前端**,跑完不影响 demo 数据
  ① eval_user_1 / EvalAlice    — 美妆 case 主测(敏感肌女)
  ② eval_user_2 / EvalBob      — 数码 + 行为回归 case 主测(高消费男)
  ③ eval_user_3 / EvalCharlie  — 业务闭环 + 边缘 case 主测(节约男)

策略:
- preferences.brand_prefer / brand_exclude 用 dataset 真实品牌,演示
  "rejected_brands 自动并入 hard_constraints.brand_exclude"(§4.3 多轮)
- eval user 默认 profile 完整;具体 case 可通过 `profile_overrides` 在 runner 里
  动态清字段(如"地址缺失下单"case 清 address)
- 跑法:`python -m scripts.seed_users` 或 `python scripts/seed_users.py`
- 幂等:已存在则 merge 更新

注意:V1 无 auth,前端顶栏下拉只显示 demo_user_*。
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


DEMO_USERS: list[dict[str, Any]]
EVAL_USERS: list[dict[str, Any]]


DEMO_USERS = [
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
                # 通用(品牌取自 dataset 真实清单)
                "brand_prefer": ["雅诗兰黛", "资生堂"],
                "brand_exclude": ["The Ordinary", "完美日记"],
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
                # 通用(品牌取自 dataset 真实清单)
                "brand_prefer": ["Apple 苹果"],
                "brand_exclude": ["OPPO", "vivo"],
            },
        },
    },
    {
        "user_id": "demo_user_3",
        "display_name": "Charlie",
        "profile": {
            "age": 24,
            "gender": "male",
            "height_cm": 175.0,
            "weight_kg": 62.0,
            "consumption_tier": "节约型",
            "recipient_name": "Charlie Liu",
            "phone": "13800000003",
            "address": "杭州市西湖区文三路 388 号 8 楼",
            "preferences": {
                # 服饰类目特化
                "clothing_size": "M",
                "shoe_size": 42,
                "style_pref": ["简约", "运动"],
                # 食品类目特化
                "dietary_restrictions": ["素食", "低糖"],
                # 通用(品牌取自 dataset 真实清单)
                "brand_prefer": ["安踏", "李宁"],
                "brand_exclude": ["始祖鸟", "露露乐蒙"],
            },
        },
    },
]


EVAL_USERS = [
    {
        "user_id": "eval_user_1",
        "display_name": "EvalAlice",
        "profile": {
            "age": 27,
            "gender": "female",
            "height_cm": 165.0,
            "weight_kg": 52.0,
            "consumption_tier": "中等",
            "recipient_name": "Eval Alice",
            "phone": "13900000001",
            "address": "上海市浦东新区张江高科技园区评测路 1 号",
            "preferences": {
                "skin_type": "敏感肌",
                "skin_concerns": ["保湿", "舒缓"],
                "fragrance_pref": "无香",
                "brand_prefer": ["雅诗兰黛"],
                "brand_exclude": ["完美日记"],
            },
        },
    },
    {
        "user_id": "eval_user_2",
        "display_name": "EvalBob",
        "profile": {
            "age": 32,
            "gender": "male",
            "height_cm": 178.0,
            "weight_kg": 72.0,
            "consumption_tier": "高消费",
            "recipient_name": "Eval Bob",
            "phone": "13900000002",
            "address": "北京市海淀区清华东路评测大厦 2 号",
            "preferences": {
                "usage": ["办公", "摄影"],
                "os_pref": "iOS",
                "brand_prefer": ["Apple 苹果"],
                "brand_exclude": ["OPPO"],
            },
        },
    },
    {
        "user_id": "eval_user_3",
        "display_name": "EvalCharlie",
        "profile": {
            "age": 24,
            "gender": "male",
            "height_cm": 175.0,
            "weight_kg": 62.0,
            "consumption_tier": "节约型",
            "recipient_name": "Eval Charlie",
            "phone": "13900000003",
            "address": "广州市天河区评测街 3 号",
            "preferences": {
                "clothing_size": "M",
                "shoe_size": 42,
                "style_pref": ["简约", "运动"],
                "dietary_restrictions": ["低糖"],
                "brand_prefer": ["安踏"],
                "brand_exclude": ["始祖鸟"],
            },
        },
    },
]


async def seed_users() -> None:
    """幂等 seed:demo + eval 用户 + profile,merge 模式不破坏已有数据。"""
    # 确保表已建(首跑 / DB reset 后)
    await create_all()

    all_users = DEMO_USERS + EVAL_USERS
    async with AsyncSessionLocal() as session:
        for entry in all_users:
            await UserRepo.upsert_user(
                session, entry["user_id"], entry["display_name"]
            )
            # FK 依赖 users 行先存在,flush 一下让 merge 落到 DB
            await session.flush()
            await UserRepo.upsert_profile(session, entry["user_id"], **entry["profile"])
        await session.commit()

    print(f"[seed_users] OK: seeded {len(DEMO_USERS)} demo + {len(EVAL_USERS)} eval users")
    for entry in DEMO_USERS:
        print(f"  - DEMO  {entry['user_id']} ({entry['display_name']})")
    for entry in EVAL_USERS:
        print(f"  - EVAL  {entry['user_id']} ({entry['display_name']})")


def main() -> None:
    asyncio.run(seed_users())


if __name__ == "__main__":
    main()
