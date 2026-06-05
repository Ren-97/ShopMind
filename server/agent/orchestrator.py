"""Agent 主循环(§4.6.4 + §4.6.9 + §4.6.10)。

职责:
- 装载 profile + session_state + 最近 N 轮 chat_history → initial messages
- Tool loop(最多 MAX_AGENT_TURNS)
  - 调 stream_agent_turn → 流式 yield thinking/text + 最终 Message
  - 若 stop_reason == "tool_use":并行 / 串行 execute_tool → 回灌 tool_result → 继续
  - 否则:break
- 异常包装(§4.6.9 四层防御)
- 更新 session_state(§4.6.6 规则提取)
- 持久化 chat_history(user + assistant 一对)

yield 出的是 AgentEvent(Pydantic),由 API 层(chunk 6)序列化成 SSE wire。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any, Literal

import structlog
from anthropic.types import Message, ToolUseBlock
from pydantic import BaseModel, ConfigDict, Field

from server import config
from server.agent.fallback_messages import FALLBACK_MESSAGES
from server.agent.session_state import (
    SessionState,
    SessionStateStore,
    update_session_state_after_turn,
)
from server.domain.types import QueryPlan
from server.llm.agent_call import StreamChunk, stream_agent_turn
from server.storage.catalog_repo import CatalogRepo
from server.storage.user_repo import UserRepo
from server.tools.base import (
    AgentDeps,
    Tool,
    ToolResult,
    execute_tool,
    serialize_payload_for_claude,
    tool_specs_for_anthropic,
)

log = structlog.get_logger("shopmind.orchestrator")


AgentEventType = Literal[
    "meta", "thinking", "tool_call", "card", "text", "suggestions", "done", "error"
]


class AgentEvent(BaseModel):
    """SSE 事件的中性表示(§4.7.1)。API 层(chunk 6)转 SSE wire 格式。"""

    model_config = ConfigDict(extra="forbid")

    type: AgentEventType
    data: dict[str, Any] = Field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────────
# Orchestrator
# ──────────────────────────────────────────────────────────────────────
class Orchestrator:
    """Agent 主循环。所有 deps 在构造时注入,handle_user_turn 是 per-turn 入口。"""

    def __init__(
        self,
        *,
        deps: AgentDeps,
        tool_registry: dict[str, Tool],
        session_store: SessionStateStore,
    ) -> None:
        self._deps = deps
        self._registry = tool_registry
        self._tool_specs = tool_specs_for_anthropic(tool_registry)
        self._sessions = session_store
        # 本进程内已从 DB 重建过中期记忆的 session(每进程每 session 只 rehydrate 一次)
        self._hydrated: set[str] = set()

    async def handle_user_turn(
        self,
        *,
        user_query: str,
        user_id: str,
        session_id: str,
    ) -> AsyncIterator[AgentEvent]:
        """处理一个用户 turn,流式 yield AgentEvent。

        Catastrophic 防御(§4.6.9 第 3 层)— 任何未捕获异常都转成 error event。
        """
        turn_id = f"turn-{uuid.uuid4().hex[:8]}"
        yield AgentEvent(
            type="meta",
            data={"session_id": session_id, "turn_id": turn_id, "user_id": user_id},
        )

        try:
            async for ev in self._run_turn_inner(
                user_query=user_query,
                user_id=user_id,
                session_id=session_id,
            ):
                yield ev
        except Exception as e:  # noqa: BLE001 — 第 3 层防御
            log.exception("catastrophic_chat_failure", error=str(e))
            yield AgentEvent(
                type="error",
                data={"code": "internal_error", "msg": FALLBACK_MESSAGES["catastrophic"]},
            )
            yield AgentEvent(type="done", data={"finish_reason": "error"})

    # ──────────────────────────────────────────────────────────────
    async def _run_turn_inner(
        self,
        *,
        user_query: str,
        user_id: str,
        session_id: str,
    ) -> AsyncIterator[AgentEvent]:
        state = self._sessions.get(session_id)
        # 进程重启会清空内存中期记忆,但客户端是持久 session_id(重开 app 不变)。
        # 首次见到某 session 时,从持久化的 chat_history.card_refs 把"展示过的商品 + id"
        # 重建回内存,否则重启后指代("就买蒙牛的"/"小黑瓶")会因 product_index 为空而断。
        await self._ensure_session_hydrated(
            user_id=user_id, session_id=session_id, state=state
        )
        # context 段:profile + session_state(动态部分,不入 prompt cache)
        extra_context, profile_dict, session_dict = await self._build_turn_context(
            user_id, state
        )
        # per-turn deps 副本:把 profile / session 透传给 tool(search → Planner 指代消解)
        turn_deps = replace(
            self._deps,
            session_snapshot=session_dict,
            user_profile=profile_dict or None,
        )

        # initial messages:历史 N 轮 + 当前 user query
        messages = await self._build_initial_messages(
            user_id=user_id, session_id=session_id, user_query=user_query
        )

        all_tool_results: list[tuple[str, ToolResult]] = []
        last_plan: QueryPlan | None = None  # 由 search_products tool 通过 result.meta 回传
        assistant_text_accum: list[str] = []

        finish_reason = "stop"

        for turn_idx in range(config.MAX_AGENT_TURNS):
            final_message: Message | None = None
            async for chunk in stream_agent_turn(
                messages=messages,
                tools=self._tool_specs,
                extra_context=extra_context,
            ):
                if chunk.type == "thinking":
                    yield AgentEvent(type="thinking", data={"delta": chunk.data["delta"]})
                elif chunk.type == "text":
                    delta = chunk.data["delta"]
                    assistant_text_accum.append(delta)
                    yield AgentEvent(type="text", data={"delta": delta})
                elif chunk.type == "final":
                    final_message = chunk.data["message"]

            assert final_message is not None, "stream_agent_turn 必须 yield final chunk"

            if final_message.stop_reason != "tool_use":
                break

            # 执行 tool calls(V1 串行)
            tool_results_for_round: list[tuple[str, ToolResult, ToolUseBlock]] = []
            for block in final_message.content:
                if not isinstance(block, ToolUseBlock):
                    continue
                yield AgentEvent(
                    type="tool_call",
                    data={"name": block.name, "args": block.input},
                )
                result = await execute_tool(
                    block,
                    user_id=user_id,
                    deps=turn_deps,
                    registry=self._registry,
                )
                tool_results_for_round.append((block.name, result, block))
                # SSE 副作用:cards 立刻 emit;suggestions 等本轮所有 tool 跑完一起 emit
                for card in result.cards:
                    yield AgentEvent(type="card", data=card)

            # suggestions:把同一轮的全部 suggestions 合并成一条 event(§4.6.11)
            merged_suggestions: list[dict[str, Any]] = []
            for _name, result, _block in tool_results_for_round:
                merged_suggestions.extend(result.suggestions)
            if merged_suggestions:
                yield AgentEvent(
                    type="suggestions", data={"items": merged_suggestions}
                )

            # 累积进 session_state 用 + 提取 search 回传的 plan
            for name, r, _ in tool_results_for_round:
                all_tool_results.append((name, r))
                if name == "search_products" and r.meta.get("plan"):
                    try:
                        last_plan = QueryPlan.model_validate(r.meta["plan"])
                    except Exception:  # noqa: BLE001 — plan 解析失败不影响主流程
                        log.warning("orchestrator_plan_parse_failed")

            # 回灌 tool_result:assistant Message + 紧跟的 user tool_result
            messages.append({"role": "assistant", "content": final_message.content})
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": serialize_payload_for_claude(result.payload),
                            "is_error": result.is_error,
                        }
                        for _name, result, block in tool_results_for_round
                    ],
                }
            )
        else:
            # MAX_TURNS 兜底(§4.6.9 第 2 层)
            log.warning("agent_max_turns_reached", turns=config.MAX_AGENT_TURNS)
            fallback = FALLBACK_MESSAGES["agent_max_turns"]
            assistant_text_accum.append(fallback)
            yield AgentEvent(type="text", data={"delta": fallback})
            finish_reason = "max_turns"

        # ── 收尾 ──
        update_session_state_after_turn(
            state, plan=last_plan, tool_results=all_tool_results
        )
        self._sessions.set(session_id, state)

        # 持久化 chat_history(一对 user/assistant)
        await self._persist_turn(
            user_id=user_id,
            session_id=session_id,
            user_query=user_query,
            assistant_text="".join(assistant_text_accum),
            tool_results=all_tool_results,
        )

        yield AgentEvent(type="done", data={"finish_reason": finish_reason})

    # ──────────────────────────────────────────────────────────────
    async def _ensure_session_hydrated(
        self, *, user_id: str, session_id: str, state: SessionState
    ) -> None:
        """进程内首次见到某 session 时,从持久化历史重建中期记忆(product_index 等)。

        中期记忆是进程内存(SessionStateStore 不持久化),进程重启/reload 即丢;但
        chat_history.card_refs 持久化了每轮展示的 product_id。这里遍历它重建:
        discussed_products(全量并集)/ last_shown_products(最近一轮)/ product_index
        (id→title/brand,供 Planner 指代消解)。每进程每 session 只跑一次。
        """
        if session_id in self._hydrated:
            return
        self._hydrated.add(session_id)
        # 本进程内已积累过状态(非重启场景)→ 不覆盖
        if state.discussed_products or state.last_shown_products:
            return

        async with self._deps.session_factory() as session:
            history = await UserRepo.list_recent_turns(
                session, user_id, session_id, n_turns=config.AGENT_RECENT_TURNS * 2
            )
            discussed: set[str] = set()
            last_shown: list[str] = []
            for m in history:  # 时间正序
                refs = m.card_refs or {}
                products = list(refs.get("products") or [])
                compare = list(refs.get("compare") or [])
                ids = products + compare
                if not ids:
                    continue
                discussed |= set(ids)
                last_shown = products or compare  # 覆盖:保留最近一轮展示的
            if not discussed:
                return
            indexed = await CatalogRepo.list_products_by_ids(
                session, sorted(discussed), include_inactive=True
            )

        state.discussed_products |= discussed
        state.last_shown_products = last_shown
        for p in indexed:
            info: dict[str, str] = {}
            if p.title:
                info["title"] = p.title
            if p.brand:
                info["brand"] = p.brand
            if info:
                state.product_index[p.product_id] = info
        log.info(
            "session_rehydrated",
            session_id=session_id,
            n_discussed=len(discussed),
            n_last_shown=len(last_shown),
        )

    async def _build_turn_context(
        self, user_id: str, state: SessionState
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """构造本轮上下文,一次性产出三样(避免重复查 profile):

        - `extra_context`:profile + session_state 渲染成一段文本(给 Agent system 看)
        - `profile_dict`:结构化 profile(经 turn_deps 透传给 search → Planner)
        - `session_dict`:session_state.render_for_planner()(同上)
        """
        async with self._deps.session_factory() as session:
            profile = await UserRepo.get_profile(session, user_id)

        profile_dict: dict[str, Any] = {}
        if profile is not None:
            profile_dict = {
                k: v
                for k, v in {
                    "age": profile.age,
                    "gender": profile.gender,
                    "height_cm": profile.height_cm,
                    "weight_kg": profile.weight_kg,
                    "consumption_tier": profile.consumption_tier,
                    "recipient_name": profile.recipient_name,
                    "phone": profile.phone,
                    "address": profile.address,
                    "preferences": profile.preferences,
                }.items()
                if v is not None and v != {}
            }

        session_dict = state.render_for_planner()
        ctx = {
            "user_profile": profile_dict,
            "session_state": session_dict,
        }
        extra_context = f"[上下文]\n{json.dumps(ctx, ensure_ascii=False)}"
        return extra_context, profile_dict, session_dict

    async def _build_initial_messages(
        self,
        *,
        user_id: str,
        session_id: str,
        user_query: str,
    ) -> list[dict[str, Any]]:
        """历史 N 轮 + 当前 user query → Anthropic messages 格式。"""
        async with self._deps.session_factory() as session:
            history = await UserRepo.list_recent_turns(
                session, user_id, session_id, n_turns=config.AGENT_RECENT_TURNS * 2
            )

        messages: list[dict[str, Any]] = []
        for m in history:
            if m.role not in ("user", "assistant"):
                continue
            if not m.content:
                continue
            messages.append({"role": m.role, "content": m.content})

        messages.append({"role": "user", "content": user_query})
        return messages

    async def _persist_turn(
        self,
        *,
        user_id: str,
        session_id: str,
        user_query: str,
        assistant_text: str,
        tool_results: list[tuple[str, ToolResult]],
    ) -> None:
        """落 chat_history(user + assistant 一对)。

        - `tool_calls` 摘要存 JSON(observability 用)
        - `card_refs` 存本轮 emit 的卡片商品 / 订单 ID 引用(B+:历史里渲染卡片用)
          只记录 product / compare_table / order 三种;cart / checkout 是临时态不存
        """
        tool_calls_summary: list[dict[str, Any]] = [
            {"name": name, "is_error": r.is_error}
            for name, r in tool_results
        ]
        card_refs = self._collect_card_refs(tool_results)
        async with self._deps.session_factory() as session:
            await UserRepo.append_message(
                session, user_id, session_id, role="user", content=user_query
            )
            await UserRepo.append_message(
                session,
                user_id,
                session_id,
                role="assistant",
                content=assistant_text,
                tool_calls=tool_calls_summary or None,
                card_refs=card_refs,
            )
            await session.commit()

    @staticmethod
    def _collect_card_refs(
        tool_results: list[tuple[str, ToolResult]],
    ) -> dict[str, Any] | None:
        """从本轮 tool_results 抽出商品/订单引用,V2 历史渲染时实时拉。

        - product card → 加入 products[](按 emit 顺序保序)
        - compare_table → 取 headers 的 product_ids 加入 compare[]
        - order card → 取 order_id 单值
        - cart / checkout / 其它 → 不存(临时态,历史里看的是当前)
        """
        products: list[str] = []
        compare: list[str] = []
        order_id: str | None = None
        for _, tr in tool_results:
            for card in tr.cards:
                t = card.get("type")
                d = card.get("data", {}) or {}
                if t == "product":
                    pid = d.get("product_id")
                    if isinstance(pid, str) and pid:
                        products.append(pid)
                elif t == "compare_table":
                    for h in d.get("headers") or []:
                        pid = h.get("product_id") if isinstance(h, dict) else None
                        if isinstance(pid, str) and pid:
                            compare.append(pid)
                elif t == "order":
                    oid = d.get("order_id")
                    if isinstance(oid, str) and oid:
                        order_id = oid
        refs: dict[str, Any] = {}
        if products:
            refs["products"] = products
        if compare:
            refs["compare"] = compare
        if order_id:
            refs["order"] = order_id
        return refs or None


__all__ = ["AgentEvent", "Orchestrator"]
