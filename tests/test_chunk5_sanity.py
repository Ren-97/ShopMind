"""Chunk 5 Sanity Test — Agent + Tools 端到端(无外部 LLM 调用)。

覆盖:
- 所有 chunk5 模块 import 不挂
- 7 个 tool 都注册;tool_specs_for_anthropic 输出合法 Anthropic schema
- execute_tool 异常包装(未知 tool / tool 内部 ToolError)
- compute_highlight 纯规则函数
- update_session_state_after_turn 规则提取
- Orchestrator 端到端 — Fake Anthropic client + 真实 tools + 真实 DB(Postgres test 库)

不测的(留 Eval / 集成测试):
- 真打 Sonnet streaming(成本 + 不确定性)
- 真实检索全链路(chunk3/4 已测)
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ──────────────────────────────────────────────────────────────
# import sanity
# ──────────────────────────────────────────────────────────────
def test_import_all_chunk5_modules() -> None:
    import server.agent  # noqa: F401
    import server.agent.fallback_messages  # noqa: F401
    import server.agent.orchestrator  # noqa: F401
    import server.agent.session_state  # noqa: F401
    import server.llm.agent_call  # noqa: F401
    import server.llm.prompts.agent  # noqa: F401
    import server.tools  # noqa: F401
    import server.tools.base  # noqa: F401
    import server.tools.cart  # noqa: F401
    import server.tools.compare  # noqa: F401
    import server.tools.order  # noqa: F401
    import server.tools.preference  # noqa: F401
    import server.tools.recall  # noqa: F401
    import server.tools.search  # noqa: F401
    import server.tools.suggestions  # noqa: F401


# ──────────────────────────────────────────────────────────────
# Tool registry + Anthropic schema
# ──────────────────────────────────────────────────────────────
def test_tool_registry_complete() -> None:
    from server.tools import build_tool_registry, tool_specs_for_anthropic

    registry = build_tool_registry()
    expected = {
        "search_products",
        "compare_products",
        "manage_cart",
        "place_order",
        "update_preference",
        "recall_history",
        "show_suggestions",
    }
    assert set(registry.keys()) == expected

    specs = tool_specs_for_anthropic(registry)
    assert len(specs) == 7
    for spec in specs:
        assert "name" in spec
        assert "description" in spec
        assert "input_schema" in spec
        schema = spec["input_schema"]
        # Pydantic v2 生成的 JSON schema 一定带 properties + type
        assert schema["type"] == "object"
        # user_id 绝对不能出现在 Claude 看到的 schema 里(§4.6.8)
        assert "user_id" not in schema.get("properties", {})


# ──────────────────────────────────────────────────────────────
# execute_tool 异常包装(§4.6.9 第 1 层防御)
# ──────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_execute_tool_unknown_returns_error() -> None:
    from server.tools.base import AgentDeps, execute_tool

    @dataclass
    class FakeToolUse:
        name: str
        input: dict[str, Any]

    # 用 None deps 即可,因为不会到 tool 内部
    result = await execute_tool(
        FakeToolUse(name="nonexistent_tool", input={}),
        user_id="demo_user_1",
        deps=AgentDeps(session_factory=None, dispatcher=None, reranker=None, base_url=""),  # type: ignore[arg-type]
        registry={},
    )
    assert result.is_error is True
    assert "未知工具" in result.payload["error"]


# ──────────────────────────────────────────────────────────────
# product_card tags_candidates(§4.7.4)
# ──────────────────────────────────────────────────────────────
def test_product_card_tags_candidates() -> None:
    """跨字段收集 + bool 友好渲染 + 黑名单字段过滤 + 不排序保 dataset 顺序。"""
    from server.tools._serializers import product_card

    payload = {
        "product_id": "p_001", "title": "测试", "brand": "B",
        "image_url": None, "base_price": 100.0, "in_stock": True, "skus": [],
        "properties": {
            "effects": ["保湿", "舒缓"],
            "suitable_skin": ["敏感肌", "干皮"],
            "not_suitable_skin": ["油皮"],     # 黑名单字段:绝不进 chip
            "contains_alcohol": False,         # bool → "无酒精"
            "contains_fragrance": True,        # bool → "含香精"
            "age_group": "25+",                # str
            "screen_size": 6.1,                # 数值 → 跳过
        },
    }
    card = product_card(payload)
    cand = card["data"]["tags_candidates"]

    # 黑名单字段过滤
    assert "油皮" not in cand

    # bool 友好渲染
    assert "无酒精" in cand
    assert "含香精" in cand

    # 数值字段跳过
    assert 6.1 not in cand and "6.1" not in cand

    # 跨字段收集 + str + list[str]
    for expected in ["保湿", "舒缓", "敏感肌", "干皮", "25+"]:
        assert expected in cand

    # 不排序:保留 dataset 输入顺序(effects 在 suitable_skin 前)
    assert cand.index("保湿") < cand.index("敏感肌")

    # 去重:重复 value 只进一次
    payload2 = {**payload, "properties": {
        "effects": ["保湿"], "suitable_skin": ["保湿"]   # 故意重复
    }}
    cand2 = product_card(payload2)["data"]["tags_candidates"]
    assert cand2.count("保湿") == 1

    # 跨类目:数码也能进
    payload3 = {**payload, "properties": {
        "os": "iOS", "cpu": "A17 Pro", "key_use_cases": ["摄影", "办公"],
    }}
    cand3 = product_card(payload3)["data"]["tags_candidates"]
    assert set(cand3) == {"iOS", "A17 Pro", "摄影", "办公"}


# ──────────────────────────────────────────────────────────────
# compute_highlight 规则
# ──────────────────────────────────────────────────────────────
def test_compute_highlight_rules() -> None:
    """只对客观事实高亮:价格 winner / 缺货 warning。主观属性(含酒精等)不高亮。"""
    from server.tools.compare import _highlight_min, _highlight_out_of_stock

    # 价格 winner:全同 → None;不同 → min indices(支持平手多 index)
    assert _highlight_min([100.0, 100.0, 100.0]) is None
    h = _highlight_min([720.0, 850.0, 720.0])
    assert h is not None and h["type"] == "winner"
    assert sorted(h["indices"]) == [0, 2]

    # 缺货 warning:in_stock=False 标 warning;全在售 / 全缺货 → None
    assert _highlight_out_of_stock([True, True, True]) is None
    assert _highlight_out_of_stock([False, False, False]) is None
    h2 = _highlight_out_of_stock([True, False, True])
    assert h2 is not None and h2["type"] == "warning"
    assert h2["indices"] == [1]


# ──────────────────────────────────────────────────────────────
# session_state 规则提取
# ──────────────────────────────────────────────────────────────
def test_session_state_update_from_tool_results() -> None:
    from server.agent.session_state import (
        SessionState,
        update_session_state_after_turn,
    )
    from server.domain.types import HardConstraints, QueryPlan
    from server.tools.base import ToolResult

    state = SessionState()
    plan = QueryPlan(
        query_type="filtered_semantic",
        hard_constraints=HardConstraints(
            brand_exclude=["ANESSA"], price_max=500.0
        ),
        text_query="敏感肌洗面奶",
        confidence=0.9,
    )
    tool_results = [
        (
            "search_products",
            ToolResult(
                payload={
                    "products": [
                        {"product_id": "p_001"},
                        {"product_id": "p_002"},
                    ]
                }
            ),
        ),
    ]
    update_session_state_after_turn(state, plan=plan, tool_results=tool_results)

    assert "ANESSA" in state.rejected_brands
    assert state.mentioned_price_cap == 500.0
    assert state.current_topic == "敏感肌洗面奶"
    assert state.discussed_products == {"p_001", "p_002"}
    assert state.last_shown_products == ["p_001", "p_002"]

    # 第二轮:price_max 撤回为 None → 覆盖(不是累积)
    plan2 = QueryPlan(
        query_type="filtered_semantic",
        hard_constraints=HardConstraints(),
        text_query="iPhone 配件",
        confidence=0.9,
    )
    update_session_state_after_turn(state, plan=plan2, tool_results=[])
    assert state.mentioned_price_cap is None  # 覆盖
    assert "ANESSA" in state.rejected_brands  # 累积保留

    # 第三轮:用户主动收紧价格(300 覆盖之前的 500-或-None)→ 覆盖不是 max
    plan3 = QueryPlan(
        query_type="filtered_semantic",
        hard_constraints=HardConstraints(price_max=300.0),
        text_query="便宜的洗面奶",
        confidence=0.9,
    )
    update_session_state_after_turn(state, plan=plan3, tool_results=[])
    assert state.mentioned_price_cap == 300.0  # 覆盖,不取 max


# ──────────────────────────────────────────────────────────────
# 兜底文案集中模板
# ──────────────────────────────────────────────────────────────
def test_fallback_messages_complete() -> None:
    from server.agent import FALLBACK_MESSAGES

    required_keys = {
        "no_match",
        "planner_failure",
        "agent_max_turns",
        "catastrophic",
        "tool_unavailable",
        "out_of_stock",
        "off_shelf",
        "address_missing",
    }
    assert required_keys.issubset(set(FALLBACK_MESSAGES.keys()))


# ──────────────────────────────────────────────────────────────
# Order 收货三件套快照(address / recipient_name / phone)
# ──────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_order_snapshot_recipient_and_phone(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """下单后改 user_profile,旧订单卡片仍显示快照值。"""
    from server.storage.models import User, UserProfile
    from server.storage.user_repo import UserRepo
    from server.tools._serializers import order_card

    async with test_session_factory() as session:
        session.add(User(user_id="u_snap", display_name="Snap"))
        await session.flush()
        session.add(
            UserProfile(
                user_id="u_snap",
                recipient_name="原始姓名",
                phone="138-0000-0000",
                address="原始地址 100 号",
                preferences={},
            )
        )
        await session.commit()

        order = await UserRepo.create_order(
            session,
            user_id="u_snap",
            order_id="ord-snap-1",
            items=[{"sku_id": "s_x", "title": "测试", "qty": 1, "unit_price": 99.0, "subtotal": 99.0}],
            address="原始地址 100 号",
            recipient_name="原始姓名",
            phone="138-0000-0000",
            total_price=99.0,
            status="confirmed",
        )
        await session.commit()
        await session.refresh(order)

        # 用户改 profile
        await UserRepo.upsert_profile(
            session, "u_snap",
            recipient_name="新姓名", phone="139-9999-9999", address="新地址",
        )
        await session.commit()

        # 重新读 order — 期望快照不变
        fetched = await UserRepo.get_order(session, "u_snap", "ord-snap-1")
        assert fetched is not None
        assert fetched.recipient_name == "原始姓名"
        assert fetched.phone == "138-0000-0000"
        assert fetched.address == "原始地址 100 号"

        card = order_card(fetched)
        assert card["data"]["recipient_name"] == "原始姓名"
        assert card["data"]["phone"] == "138-0000-0000"
        assert card["data"]["address"] == "原始地址 100 号"


# ──────────────────────────────────────────────────────────────
# Orchestrator 端到端 — Fake LLM,真实 manage_cart(走真实 DB)
# ──────────────────────────────────────────────────────────────
@pytest_asyncio.fixture
async def seeded_db(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> async_sessionmaker[AsyncSession]:
    """灌一个最简 product + sku + user,够 manage_cart 跑通。"""
    from server.storage.models import Product, SKU, User, UserProfile

    async with test_session_factory() as session:
        session.add(
            Product(
                product_id="p_test_001",
                title="测试洗面奶",
                brand="TestBrand",
                category="美妆护肤",
                sub_category="洁面",
                base_price=99.0,
                image_path="beauty/p_test_001/main.jpg",
                in_stock=True,
                is_active=True,
                properties={"suitable_skin": ["敏感肌"], "contains_alcohol": False},
            )
        )
        session.add(
            SKU(
                sku_id="s_test_001_1",
                product_id="p_test_001",
                properties={"size": "100ml"},
                price=99.0,
            )
        )
        session.add(User(user_id="demo_user_1", display_name="Alice"))
        await session.flush()
        session.add(
            UserProfile(
                user_id="demo_user_1",
                age=27,
                gender="female",
                address="测试地址 100 号",
                preferences={"skin_type": "敏感肌"},
            )
        )
        await session.commit()
    return test_session_factory


class FakeStream:
    """模仿 anthropic AsyncMessageStreamManager:async with + async for + get_final_message。"""

    def __init__(self, events: list[Any], final_message: Any) -> None:
        self._events = events
        self._final = final_message

    async def __aenter__(self) -> "FakeStream":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    def __aiter__(self) -> AsyncIterator[Any]:
        async def gen() -> AsyncIterator[Any]:
            for ev in self._events:
                yield ev

        return gen()

    async def get_final_message(self) -> Any:
        return self._final


class _Delta:
    def __init__(self, type_: str, text: str = "", thinking: str = "") -> None:
        self.type = type_
        self.text = text
        self.thinking = thinking


class _Event:
    def __init__(self, type_: str, delta: _Delta | None = None) -> None:
        self.type = type_
        self.delta = delta


class _Usage:
    input_tokens = 100
    output_tokens = 50


class _FinalMessage:
    def __init__(self, stop_reason: str, content: list[Any]) -> None:
        self.stop_reason = stop_reason
        self.content = content
        self.usage = _Usage()


class _FakeMessagesAPI:
    """每次调 stream 返回 _stream_factory() 给的下一个 FakeStream。"""

    def __init__(self, streams: list[FakeStream]) -> None:
        self._streams = list(streams)

    def stream(self, **_kwargs: Any) -> FakeStream:  # noqa: ARG002
        return self._streams.pop(0)


class FakeAnthropic:
    def __init__(self, streams: list[FakeStream]) -> None:
        self.messages = _FakeMessagesAPI(streams)


@pytest.mark.asyncio
async def test_orchestrator_end_to_end_with_fake_llm(
    seeded_db: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """端到端:Fake Claude 调用 manage_cart(list)→ 文本回复 → done。

    覆盖:
      - tool_call event
      - card event(cart card)
      - text event(最终回复)
      - chat_history 持久化(user + assistant)
      - SessionState 更新
    """
    from anthropic.types import ToolUseBlock

    from server.agent.orchestrator import Orchestrator
    from server.agent.session_state import SessionStateStore
    from server.tools.base import AgentDeps
    from server.tools import build_tool_registry

    # 第 1 轮:Claude 决定调 manage_cart(list)
    tool_use = ToolUseBlock(
        id="toolu_test_1",
        name="manage_cart",
        input={"action": "list"},
        type="tool_use",
    )
    round1 = FakeStream(
        events=[
            _Event("content_block_delta", _Delta("thinking_delta", thinking="我先看看购物车...")),
        ],
        final_message=_FinalMessage(stop_reason="tool_use", content=[tool_use]),
    )

    # 第 2 轮:Claude 收到 tool_result,流式吐文本,end_turn
    round2 = FakeStream(
        events=[
            _Event("content_block_delta", _Delta("text_delta", text="你的购物车")),
            _Event("content_block_delta", _Delta("text_delta", text="目前是空的。")),
        ],
        final_message=_FinalMessage(stop_reason="end_turn", content=[]),
    )

    fake_client = FakeAnthropic([round1, round2])

    # patch agent_call 里的 get_anthropic_client → 返回 fake
    monkeypatch.setattr(
        "server.llm.agent_call.get_anthropic_client", lambda: fake_client
    )

    # Dispatcher / Reranker 在本测试不会用(只调 manage_cart),用 None 占位
    deps = AgentDeps(
        session_factory=seeded_db,
        dispatcher=None,  # type: ignore[arg-type]
        reranker=None,  # type: ignore[arg-type]
        base_url="http://localhost:8000",
    )
    orch = Orchestrator(
        deps=deps,
        tool_registry=build_tool_registry(),
        session_store=SessionStateStore(),
    )

    events: list[dict[str, Any]] = []
    async for ev in orch.handle_user_turn(
        user_query="看一下购物车",
        user_id="demo_user_1",
        session_id="sess-test-1",
    ):
        events.append({"type": ev.type, "data": ev.data})

    types = [e["type"] for e in events]
    # 必须有的事件
    assert types[0] == "meta"
    assert "thinking" in types
    assert "tool_call" in types
    assert "card" in types
    assert "text" in types
    assert types[-1] == "done"
    assert events[-1]["data"]["finish_reason"] == "stop"

    # 文本 deltas 拼起来 = "你的购物车目前是空的。"
    full_text = "".join(e["data"]["delta"] for e in events if e["type"] == "text")
    assert full_text == "你的购物车目前是空的。"

    # cart card 内容(空 cart)
    card_events = [e for e in events if e["type"] == "card"]
    assert len(card_events) == 1
    assert card_events[0]["data"]["type"] == "cart"
    assert card_events[0]["data"]["data"]["item_count"] == 0

    # chat_history 持久化校验
    from server.storage.user_repo import UserRepo

    async with seeded_db() as session:
        history = await UserRepo.list_messages(
            session, "demo_user_1", "sess-test-1"
        )
    assert len(history) == 2
    assert history[0].role == "user"
    assert history[0].content == "看一下购物车"
    assert history[1].role == "assistant"
    assert history[1].content == "你的购物车目前是空的。"
