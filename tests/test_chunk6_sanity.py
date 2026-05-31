"""Chunk 6 Sanity Test — FastAPI 路由 + SSE。

覆盖:
- 所有 api/ 模块 import 不挂
- main.app 路由全注册(/chat, /cart, /product/{id}, /order, /profile, /users)
- get_current_user header 解析
- AgentEvent → SSE wire 序列化
- CachedEmbedder embed_query 命中 cache + dimension 透传

不测的(留集成测试):
- 真打 Sonnet streaming(成本 + 不确定性)
- 真实 Postgres 路由(需要 docker-compose)
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ──────────────────────────────────────────────────────────────
# import sanity
# ──────────────────────────────────────────────────────────────
def test_import_all_chunk6_modules() -> None:
    import server.api  # noqa: F401
    import server.api.cart  # noqa: F401
    import server.api.chat  # noqa: F401
    import server.api.deps  # noqa: F401
    import server.api.order  # noqa: F401
    import server.api.product  # noqa: F401
    import server.api.profile  # noqa: F401
    import server.api.sse  # noqa: F401
    import server.rag.embedders.cached  # noqa: F401


# ──────────────────────────────────────────────────────────────
# 路由注册
# ──────────────────────────────────────────────────────────────
def test_routes_registered() -> None:
    from server.main import app

    paths = {r.path for r in app.routes if hasattr(r, "path")}
    required = {
        "/chat",
        "/cart",
        "/cart/{sku_id}",
        "/product/{product_id}",
        "/order",
        "/order/{order_id}",
        "/profile",
        "/users",
        "/health",
        "/readyz",
        "/whoami",
    }
    missing = required - paths
    assert not missing, f"缺少路由: {missing}"


# ──────────────────────────────────────────────────────────────
# get_current_user header 解析
# ──────────────────────────────────────────────────────────────
def test_get_current_user_header_parsing() -> None:
    from server import config
    from server.api.deps import get_current_user

    # header 缺失 → 默认
    res = asyncio.run(get_current_user(None))
    assert res == config.DEFAULT_USER_ID

    # header 有值
    res = asyncio.run(get_current_user("alice_001"))
    assert res == "alice_001"

    # 全空格 → 默认(避免空字符串污染 user_id)
    res = asyncio.run(get_current_user("   "))
    assert res == config.DEFAULT_USER_ID


# ──────────────────────────────────────────────────────────────
# SSE 序列化
# ──────────────────────────────────────────────────────────────
def test_sse_event_serialization() -> None:
    from server.agent.orchestrator import AgentEvent
    from server.api.sse import event_to_sse_dict, make_done_sse, make_error_sse

    ev = AgentEvent(type="text", data={"delta": "你好,世界"})
    out = event_to_sse_dict(ev)
    assert out["event"] == "text"
    parsed = json.loads(out["data"])
    assert parsed == {"delta": "你好,世界"}
    # ensure_ascii=False 保证中文不转义
    assert "你好" in out["data"]

    err = make_error_sse("no_match", "没找到符合的")
    assert err["event"] == "error"
    assert json.loads(err["data"]) == {"code": "no_match", "msg": "没找到符合的"}

    done = make_done_sse("stop")
    assert done["event"] == "done"
    assert json.loads(done["data"]) == {"finish_reason": "stop"}


# ──────────────────────────────────────────────────────────────
# CachedEmbedder
# ──────────────────────────────────────────────────────────────
class _FakeEmbedder:
    """记录调用次数的伪 Embedder。"""

    dimension = 3072

    def __init__(self) -> None:
        self.query_calls = 0
        self.doc_calls = 0

    async def embed_query(self, text: str) -> list[float]:
        self.query_calls += 1
        return [float(len(text))] * self.dimension

    async def embed_documents(self, texts: Any) -> list[list[float]]:
        self.doc_calls += 1
        return [[float(len(t))] * self.dimension for t in texts]


@pytest.mark.asyncio
async def test_cached_embedder_hits_cache() -> None:
    from server.cache.in_memory import InMemoryLRUCache
    from server.rag.embedders.cached import CachedEmbedder

    inner = _FakeEmbedder()
    cache = InMemoryLRUCache(maxsize=10, ttl=None)
    cached = CachedEmbedder(inner, cache)

    assert cached.dimension == inner.dimension

    v1 = await cached.embed_query("敏感肌洗面奶")
    v2 = await cached.embed_query("敏感肌洗面奶")
    assert v1 == v2
    assert inner.query_calls == 1  # 第二次命中 cache

    # 不同 query → miss
    await cached.embed_query("油皮 toner")
    assert inner.query_calls == 2

    # embed_documents 透传不走 cache(批量去重价值低,见 cached.py)
    await cached.embed_documents(["a", "b", "c"])
    await cached.embed_documents(["a", "b", "c"])
    assert inner.doc_calls == 2


# ──────────────────────────────────────────────────────────────
# Pydantic IO models
# ──────────────────────────────────────────────────────────────
def test_chat_request_validation() -> None:
    from pydantic import ValidationError

    from server.api.chat import ChatRequest

    # 正常
    req = ChatRequest(query="推荐洗面奶", session_id="s-1")
    assert req.query == "推荐洗面奶"

    # 空 query 拒绝
    with pytest.raises(ValidationError):
        ChatRequest(query="", session_id=None)

    # 多余字段拒绝(防客户端误传)
    with pytest.raises(ValidationError):
        ChatRequest(query="x", session_id=None, extra="boom")  # type: ignore[call-arg]


def test_cart_add_body_validation() -> None:
    from pydantic import ValidationError

    from server.api.cart import CartAddBody, CartUpdateBody

    body = CartAddBody(sku_id="s_001")
    assert body.qty == 1  # 默认

    with pytest.raises(ValidationError):
        CartAddBody(sku_id="s_001", qty=0)  # qty < 1 拒绝

    with pytest.raises(ValidationError):
        CartUpdateBody(qty=0)
