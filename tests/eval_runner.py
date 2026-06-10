"""Eval Runner — in-process 跑 case,setup/teardown,事件收集。

为啥 in-process(不走 HTTP):
- 走 HTTP/SSE 拿不到完整 ToolResult.payload(只能看到简版 card)
- session_state 是后端进程内 dict,跨进程不可见
- in-process 直接 import Orchestrator,什么都能读

每个 case 跑前 cleanup 4 件事:
1. SessionStateStore.clear(session_id) — 中期记忆清空
2. UserRepo.clear_cart(user_id) — cart_items wipe
3. delete orders / chat_history for user — 业务历史清空
4. profile reset 到 EVAL_USERS baseline + apply profile_overrides

然后注入 case 指定的种子:
- session_state_seed → SessionStateStore.set
- cart_seed → UserRepo.add_to_cart 多次
- history → UserRepo.append_message 多次(同 session_id,让 orchestrator 看见)
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete

from server import config
from server.agent.orchestrator import Orchestrator
from server.agent.session_state import SessionState, SessionStateStore
from server.cache.in_memory import InMemoryLRUCache
from server.domain.types import QueryPlan
from server.llm.planner import plan_query
from server.rag.embedders import get_embedder
from server.rag.embedders.cached import CachedEmbedder
from server.rag.reranking.llm_reranker import LLMReranker
from server.rag.retrieval.dispatcher import build_default_dispatcher
from server.rag.sparse import JiebaBM25Encoder
from server.storage.db import AsyncSessionLocal
from server.storage.models import ChatHistory, Order
from server.storage.user_repo import UserRepo
from server.storage.vector_index import get_vector_index
from server.tools import build_tool_registry
from server.tools.base import AgentDeps

# 从 seed 脚本拿基线 profile,reset 用
from scripts.seed_users import EVAL_USERS


# ──────────────────────────────────────────────────────────────────────
# 数据模型
# ──────────────────────────────────────────────────────────────────────
class CaseRunResult(BaseModel):
    """一个 case 跑完的全部观测。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    case_id: str
    user_id: str
    session_id: str
    query: str
    # 收集到的事件
    tool_calls: list[tuple[str, dict]] = Field(default_factory=list)
    cards: list[dict] = Field(default_factory=list)
    # tool 完整 payload(ProductSummary 全字段:skus / properties / matched_chunks)。
    # L3 judge 的真相源 —— 判幻觉要比对"Agent 实际拿到的资料",而非 lean card 或 dataset。
    tool_payloads: list[tuple[str, dict]] = Field(default_factory=list)
    assistant_text: str = ""
    # 后状态
    plan: QueryPlan | None = None  # L1 单独跑 Planner 拿到的 plan(如果 case 测 L1)
    session_state_after: dict = Field(default_factory=dict)
    cart_after: list[dict] = Field(default_factory=list)
    profile_before: dict = Field(default_factory=dict)
    profile_after: dict = Field(default_factory=dict)
    # 错误
    error: str | None = None
    # Timing(L4)
    elapsed_ms: int = 0


# ──────────────────────────────────────────────────────────────────────
# Runner
# ──────────────────────────────────────────────────────────────────────
_DEFAULT_CASES_PATH = Path(__file__).parent / "eval_cases.json"


class EvalRunner:
    """单例 runner — 装配 Orchestrator + 加载 cases + per-case 隔离跑批。"""

    def __init__(self, cases_path: Path | None = None) -> None:
        self._cases_path = cases_path or _DEFAULT_CASES_PATH
        self._orchestrator: Orchestrator | None = None
        self._session_store: SessionStateStore | None = None
        self._cases: list[dict] = []
        # tool payload 累加器(per-case,跑前 clear)。包在 tool.run 外层捕获。
        self._captured_payloads: list[tuple[str, dict]] = []
        # baseline profile lookup: user_id → profile dict
        self._baseline_profiles: dict[str, dict] = {
            entry["user_id"]: entry["profile"] for entry in EVAL_USERS
        }

    # ──────────────────────────────────────────────────────────
    async def setup(self) -> None:
        """装配 Orchestrator(模仿 server/api/deps.py:_build_orchestrator)+ 加载 cases。"""
        # Embedding LRU + Embedder
        embedding_cache = InMemoryLRUCache(maxsize=config.EMBEDDING_CACHE_SIZE, ttl=None)
        embedder = CachedEmbedder(get_embedder(), embedding_cache)

        sparse_encoder = JiebaBM25Encoder()
        vector_index = get_vector_index()

        dispatcher = build_default_dispatcher(
            session_factory=AsyncSessionLocal,
            vector_index=vector_index,
            embedder=embedder,
            sparse_encoder=sparse_encoder,
            cache=None,
        )
        reranker = LLMReranker(session_factory=AsyncSessionLocal)

        deps = AgentDeps(
            session_factory=AsyncSessionLocal,
            dispatcher=dispatcher,
            reranker=reranker,
            base_url=config.BASE_URL,
        )

        registry = build_tool_registry()
        self._install_payload_capture(registry)

        self._session_store = SessionStateStore()
        self._orchestrator = Orchestrator(
            deps=deps,
            tool_registry=registry,
            session_store=self._session_store,
        )

        # 加载 cases
        data = json.loads(self._cases_path.read_text(encoding="utf-8"))
        self._cases = data["cases"]

    def _install_payload_capture(self, registry: dict[str, Any]) -> None:
        """给每个 tool 的 `run` 包一层,把 ToolResult.payload 抓进 self._captured_payloads。

        实例级覆盖(`tool.run = wrapped`):只影响本 runner 持有的这组 tool 实例,
        不改 Tool 类、不碰 orchestrator / execute_tool / SSE。orchestrator 调
        `tool.run(...)` 命中实例属性(实例属性函数不绑 self,故 wrapped 无 self 参数)。
        sink 用同一 list 对象,run_case 跑前 `.clear()` 复用(不重新赋值,保持闭包引用有效)。
        """
        sink = self._captured_payloads
        for tool in registry.values():
            original_run = tool.run
            tname = tool.name

            async def wrapped(_orig=original_run, _name=tname, **kwargs):  # noqa: ANN003
                result = await _orig(**kwargs)
                sink.append((_name, dict(result.payload)))
                return result

            tool.run = wrapped  # type: ignore[method-assign]

    async def teardown(self) -> None:
        """目前没什么可关的(Orchestrator 没持有外部资源)。预留 hook。"""
        pass

    # ──────────────────────────────────────────────────────────
    def list_cases(
        self,
        *,
        tags: list[str] | None = None,
        case_ids: list[str] | None = None,
        layers: list[int] | None = None,
    ) -> list[dict]:
        """按过滤条件返回 case 子集。"""
        filtered = self._cases
        if case_ids:
            filtered = [c for c in filtered if c["id"] in case_ids]
        if tags:
            tag_set = set(tags)
            filtered = [c for c in filtered if tag_set & set(c.get("tags", []))]
        if layers:
            layer_set = set(layers)
            filtered = [c for c in filtered if layer_set & set(c.get("layers", []))]
        return filtered

    async def run_case_by_id(self, case_id: str) -> CaseRunResult:
        case = next((c for c in self._cases if c["id"] == case_id), None)
        if case is None:
            raise KeyError(f"case not found: {case_id}")
        return await self.run_case(case)

    # ──────────────────────────────────────────────────────────
    async def run_case(self, case: dict) -> CaseRunResult:
        """跑一个 case:cleanup → seed → run → collect → return。"""
        assert self._orchestrator is not None
        assert self._session_store is not None

        case_id = case["id"]
        user_id = case["user_id"]
        query = case["query"]
        session_id = f"eval-{case_id}-{uuid.uuid4().hex[:6]}"

        started = time.perf_counter()
        result = CaseRunResult(
            case_id=case_id, user_id=user_id, session_id=session_id, query=query
        )
        self._captured_payloads.clear()  # 本 case 的 payload 从空开始累加

        try:
            # 1. Cleanup + reset profile + apply overrides
            await self._reset_user_state(
                user_id=user_id,
                profile_overrides=case.get("profile_overrides"),
            )

            # 2. Inject seeds
            profile_before = await self._read_profile(user_id)
            result.profile_before = profile_before

            if case.get("session_state_seed"):
                self._apply_session_state_seed(session_id, case["session_state_seed"])

            if case.get("cart_seed"):
                await self._apply_cart_seed(user_id, case["cart_seed"])

            if case.get("history"):
                await self._apply_history(user_id, session_id, case["history"])
                # 用生产 hydration 从刚写入的 chat_history.card_refs 重建中期记忆
                # (last_shown / discussed / product_index)。这样 L1 standalone planner
                # 和 agent loop 看到的 session_state 一致,且与真实 app 重启恢复路径同源。
                # agent loop 内部再调一次会因 session_id 已在 _hydrated 集合而直接跳过。
                await self._orchestrator._ensure_session_hydrated(
                    user_id=user_id,
                    session_id=session_id,
                    state=self._session_store.get(session_id),
                )

            # 3. (L1 only)单独跑 Planner 拿 plan
            if 1 in case.get("layers", []):
                try:
                    result.plan = await self._run_planner_standalone(
                        user_id=user_id, session_id=session_id, query=query
                    )
                except Exception as e:  # noqa: BLE001
                    # Planner 挂了不阻塞 L2,记下 error 让 metric 失败
                    result.error = f"planner_standalone_failed: {e}"

            # 4. 跑完整 agent loop,收集事件
            await self._consume_agent_events(
                result=result,
                user_id=user_id,
                session_id=session_id,
                query=query,
            )

            # 5. 读后状态
            result.session_state_after = self._session_store.get(session_id).render_for_planner()
            result.cart_after = await self._read_cart(user_id)
            result.profile_after = await self._read_profile(user_id)
            result.tool_payloads = list(self._captured_payloads)

        except Exception as e:  # noqa: BLE001 — runner 不挂,case 内部错误记到 result
            result.error = f"runner_crash: {type(e).__name__}: {e}"

        result.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return result

    # ──────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────
    async def _reset_user_state(
        self,
        *,
        user_id: str,
        profile_overrides: dict | None,
    ) -> None:
        """每 case 跑前:profile reset + cart wipe + orders wipe + chat_history wipe。

        SessionStateStore 用 session_id 隔离(每 case 用新的 session_id),不需要单独清。
        """
        baseline = self._baseline_profiles.get(user_id, {})
        merged = dict(baseline)
        if profile_overrides:
            for k, v in profile_overrides.items():
                merged[k] = v  # v=None 会清空该字段

        async with AsyncSessionLocal() as s:
            # Reset profile(upsert with full baseline)
            await UserRepo.upsert_profile(s, user_id, **merged)
            # Wipe cart
            await UserRepo.clear_cart(s, user_id)
            # Wipe orders
            await s.execute(delete(Order).where(Order.user_id == user_id))
            # Wipe chat_history for this user(跨 case 防污染)
            await s.execute(delete(ChatHistory).where(ChatHistory.user_id == user_id))
            await s.commit()

    def _apply_session_state_seed(self, session_id: str, seed: dict) -> None:
        """注入"无法从 chat_history 重建"的派生型 session 状态。

        商品引用类(last_shown / discussed / product_index)**不在这里** —— 那些走 case 的
        `history` + card_refs,由生产的 `_ensure_session_hydrated` 从 DB 重建,忠实复刻 app
        重启后的恢复路径。这里只管 rejected_brands / mentioned_price_cap / current_topic:
        它们每轮从 plan 临时算、不持久化,真实生产重启后也会丢,没有 chat_history 重建路径,
        只能直接注入。
        """
        assert self._session_store is not None
        state = SessionState(
            rejected_brands=set(seed.get("rejected_brands", [])),
            mentioned_price_cap=seed.get("mentioned_price_cap"),
            current_topic=seed.get("current_topic"),
        )
        self._session_store.set(session_id, state)

    async def _apply_cart_seed(self, user_id: str, cart_seed: list[dict]) -> None:
        async with AsyncSessionLocal() as s:
            for item in cart_seed:
                await UserRepo.add_to_cart(
                    s, user_id, item["sku_id"], qty=int(item.get("qty", 1))
                )
            await s.commit()

    async def _apply_history(
        self, user_id: str, session_id: str, history: list[dict]
    ) -> None:
        """写入前置对话。assistant 轮可带 `card_refs`(展示过哪些商品),
        供 `_ensure_session_hydrated` 从中重建 last_shown / discussed / product_index。"""
        async with AsyncSessionLocal() as s:
            for msg in history:
                await UserRepo.append_message(
                    s,
                    user_id=user_id,
                    session_id=session_id,
                    role=msg["role"],
                    content=msg["content"],
                    card_refs=msg.get("card_refs"),
                )
            await s.commit()

    async def _read_profile(self, user_id: str) -> dict:
        async with AsyncSessionLocal() as s:
            p = await UserRepo.get_profile(s, user_id)
            if p is None:
                return {}
            return {
                "age": p.age,
                "gender": p.gender,
                "height_cm": p.height_cm,
                "weight_kg": p.weight_kg,
                "consumption_tier": p.consumption_tier,
                "recipient_name": p.recipient_name,
                "phone": p.phone,
                "address": p.address,
                "preferences": dict(p.preferences or {}),
            }

    async def _read_cart(self, user_id: str) -> list[dict]:
        async with AsyncSessionLocal() as s:
            items = await UserRepo.list_cart(s, user_id)
            return [{"sku_id": i.sku_id, "qty": i.qty} for i in items]

    async def _run_planner_standalone(
        self, *, user_id: str, session_id: str, query: str
    ) -> QueryPlan:
        """单独跑 Planner(L1 metric 用),不依赖 Orchestrator 内部 plan 提取。"""
        assert self._session_store is not None
        session_state = self._session_store.get(session_id).render_for_planner()
        # 与生产一致:Planner **不收 profile**(画像走后端代码合并 + reranker,不进 plan)。
        # recent_turns 留空 — eval 场景 history 已经 inject 进 chat_history,
        # 但 Planner 在 search tool 里拿 recent_turns,L1 stand-alone 这里简化处理
        plan = await plan_query(
            query,
            session_state=session_state,
            recent_turns=None,
        )
        return plan

    async def _consume_agent_events(
        self,
        *,
        result: CaseRunResult,
        user_id: str,
        session_id: str,
        query: str,
    ) -> None:
        """跑 orchestrator.handle_user_turn,收集 tool_call / card / text 事件。"""
        assert self._orchestrator is not None
        text_parts: list[str] = []
        async for ev in self._orchestrator.handle_user_turn(
            user_query=query,
            user_id=user_id,
            session_id=session_id,
        ):
            if ev.type == "tool_call":
                result.tool_calls.append((ev.data["name"], ev.data.get("args", {})))
            elif ev.type == "card":
                result.cards.append(ev.data)
            elif ev.type == "text":
                text_parts.append(ev.data.get("delta", ""))
            elif ev.type == "error":
                # 不覆盖之前 planner_standalone 的 error,只在没 error 时记录
                if result.error is None:
                    result.error = f"agent_error: {ev.data}"
        result.assistant_text = "".join(text_parts)


__all__ = ["EvalRunner", "CaseRunResult"]
