"""SessionState 中期记忆(§4.6.6)。

存储:进程内字典 `{session_id: SessionState}`,**不持久化**。
- App 重启 = 新 session_id → 自动从空开始
- 没有 LRU / TTL — V1 单进程 demo 无需考虑
- V2 → Redis 多实例共享(接口已经在 SessionStateStore)

字段语义:
- discussed_products: 全局累积(本 session 所有展示/讨论过)
- last_shown_products: 每轮覆盖(最近一轮)
- rejected_brands:    全局累积(用户拒绝过的)
- mentioned_price_cap: 每轮覆盖(可 None)
- current_topic:      每轮覆盖
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from server.domain.types import QueryPlan
from server.tools.base import ToolResult


class SessionState(BaseModel):
    """单 session 的中期记忆。"""

    model_config = ConfigDict(extra="forbid")

    discussed_products: set[str] = Field(default_factory=set)
    last_shown_products: list[str] = Field(default_factory=list)
    rejected_brands: set[str] = Field(default_factory=set)
    mentioned_price_cap: float | None = None
    current_topic: str | None = None

    def render_for_planner(self) -> dict[str, Any]:
        """给 Planner 看的精简字典(不暴露内部对象)。"""
        return {
            "discussed_products": sorted(self.discussed_products),
            "last_shown_products": list(self.last_shown_products),
            "rejected_brands": sorted(self.rejected_brands),
            "mentioned_price_cap": self.mentioned_price_cap,
            "current_topic": self.current_topic,
        }


class SessionStateStore:
    """`{session_id: SessionState}` 进程内字典(§4.6.6)。

    V1 单进程足够。V2 换 Redis 时只换这个类的实现,业务零改动。
    """

    def __init__(self) -> None:
        self._states: dict[str, SessionState] = {}

    def get(self, session_id: str) -> SessionState:
        if session_id not in self._states:
            self._states[session_id] = SessionState()
        return self._states[session_id]

    def set(self, session_id: str, state: SessionState) -> None:
        self._states[session_id] = state

    def clear(self, session_id: str) -> None:
        self._states.pop(session_id, None)


# ──────────────────────────────────────────────────────────────────────
# 规则提取更新(§4.6.6,不调 LLM)
# ──────────────────────────────────────────────────────────────────────
def update_session_state_after_turn(
    state: SessionState,
    *,
    plan: QueryPlan | None,
    tool_results: list[tuple[str, ToolResult]],
) -> SessionState:
    """每轮(user + assistant 一对)结束后,从 plan / tool_results 提取信号更新 state。

    返回**同一个**对象(就地修改),方便调用方链式更新。
    """
    # ── 来自 plan ──
    if plan is not None:
        # 累积型:rejected_brands(用户除非显式撤回)
        if plan.hard_constraints.brand_exclude:
            state.rejected_brands |= set(plan.hard_constraints.brand_exclude)
        # 覆盖型:price_cap / topic
        state.mentioned_price_cap = plan.hard_constraints.price_max
        # topic fallback 链:text_query(信息最浓) → sub_category → category → None
        # structured query 没 text_query,退到类目级仍保有话题信号
        state.current_topic = (
            plan.text_query
            or plan.hard_constraints.sub_category
            or plan.hard_constraints.category
        )

    # ── 来自 tool_results ──
    last_shown: list[str] | None = None
    for name, result in tool_results:
        if result.is_error:
            continue
        if name == "search_products":
            products = result.payload.get("products") or []
            ids = [p["product_id"] for p in products if "product_id" in p]
            if ids:
                state.discussed_products |= set(ids)
                last_shown = ids  # 覆盖
        elif name == "compare_products":
            products = result.payload.get("products") or []
            ids = [p["product_id"] for p in products if "product_id" in p]
            state.discussed_products |= set(ids)

    if last_shown is not None:
        state.last_shown_products = last_shown
    return state


__all__ = [
    "SessionState",
    "SessionStateStore",
    "update_session_state_after_turn",
]
