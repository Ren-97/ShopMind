"""Cache Protocol(§4.8.2)。

接口设计点:
- sync(不 async):in-memory dict 操作纳秒级,async 反而开销大;
  Redis V2 用 redis-py 的 sync 客户端 in event loop 也可接受(高并发再换 aioredis)。
- `set(..., ttl=None)` 保留参数对称;实际 TTL 由实现构造时决定(InMemoryLRUCache
  用 cachetools.TTLCache 的全局 ttl,Embedding 缓存 ttl=None / 检索缓存 ttl=300)。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Cache(Protocol):
    """统一 key→value 缓存接口。"""

    def get(self, key: str) -> Any | None:
        """miss → None。"""
        ...

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """写入。`ttl` 接口参数保留;实际 TTL 由实现决定。"""
        ...
