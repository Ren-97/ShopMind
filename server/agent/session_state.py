"""SessionState 中期记忆(§4.6.6)。

存储:进程内字典 `{session_id: SessionState}`,**不持久化**。
- App 重启 = 新 session_id → 自动从空开始
- 没有 LRU / TTL — V1 单进程 demo 无需考虑
- V2 → Redis 多实例共享(接口已经在 SessionStateStore)

字段语义:
- discussed_products: 全局累积(本 session 所有展示/讨论过)
- last_shown_products: 每轮覆盖(最近一轮)
- product_index:      全局累积,product_id → {title, brand};供 Planner 把
                      用户的商品名/俗称指代(如"小黑瓶")消解回 product_id 走 id_lookup
- rejected_brands:    全局累积(用户拒绝过的)
- mentioned_price_cap: 每轮覆盖(可 None)
- current_topic:      每轮覆盖
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from server.domain.types import QueryPlan
from server.indexing.brand_aliases import normalize_brand
from server.tools.base import ToolResult


class SessionState(BaseModel):
    """单 session 的中期记忆。"""

    model_config = ConfigDict(extra="forbid")

    discussed_products: set[str] = Field(default_factory=set)
    last_shown_products: list[str] = Field(default_factory=list)
    product_index: dict[str, dict[str, str]] = Field(default_factory=dict)
    rejected_brands: set[str] = Field(default_factory=set)
    mentioned_price_cap: float | None = None
    current_topic: str | None = None

    def _shown_products(self) -> list[dict[str, str]]:
        """已展示商品的 {id, title, brand} 列表,last_shown 在前、其余 discussed 在后。

        给 Planner 做"商品名/俗称 → product_id"指代消解用(id_lookup)。
        product_index 里没记到的(理论上不该发生)只给 id。
        """
        ordered: list[str] = list(self.last_shown_products)
        for pid in sorted(self.discussed_products):
            if pid not in ordered:
                ordered.append(pid)
        out: list[dict[str, str]] = []
        for pid in ordered:
            entry: dict[str, str] = {"id": pid}
            info = self.product_index.get(pid) or {}
            if info.get("title"):
                entry["title"] = info["title"]
            if info.get("brand"):
                entry["brand"] = info["brand"]
            out.append(entry)
        return out

    def render_for_planner(self) -> dict[str, Any]:
        """给 Planner 看的精简字典(不暴露内部对象)。"""
        return {
            "shown_products": self._shown_products(),
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
        # normalize 一下,避免 set 同时存 "Nike" 和 "耐克"(Planner 后续看到混乱)
        if plan.hard_constraints.brand_exclude:
            state.rejected_brands |= {
                normalize_brand(b) for b in plan.hard_constraints.brand_exclude if b
            }
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
                _index_products(state, products)
                last_shown = ids  # 覆盖
        elif name == "compare_products":
            products = result.payload.get("products") or []
            ids = [p["product_id"] for p in products if "product_id" in p]
            state.discussed_products |= set(ids)
            _index_products(state, products)

    if last_shown is not None:
        state.last_shown_products = last_shown
    return state


def _index_products(state: SessionState, products: list[dict[str, Any]]) -> None:
    """把 product payload 里的 title / brand 累积进 product_index(供指代消解)。

    payload 不一定带 title(如单测只给 product_id)→ 缺啥跳啥,不覆盖已有值。
    """
    for p in products:
        pid = p.get("product_id")
        if not pid:
            continue
        info = dict(state.product_index.get(pid, {}))
        if p.get("title"):
            info["title"] = str(p["title"])
        if p.get("brand"):
            info["brand"] = str(p["brand"])
        if info:
            state.product_index[pid] = info


__all__ = [
    "SessionState",
    "SessionStateStore",
    "update_session_state_after_turn",
]
