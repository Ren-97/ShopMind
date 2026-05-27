"""InMemoryLRUCache:cachetools 包装(§4.8.3 / §4.8.4)。

构造时决定 ttl:
- `ttl=None` → LRUCache(embedding 缓存,text→vector 永远稳定)
- `ttl=300` → TTLCache(检索缓存,catalog 变更后 300s 自动失效)
"""

from __future__ import annotations

from typing import Any

from cachetools import LRUCache, TTLCache


class InMemoryLRUCache:
    """Maxsize + 可选 TTL 的进程内缓存。"""

    def __init__(self, maxsize: int = 1000, ttl: int | None = None) -> None:
        self._ttl: int | None = ttl
        self._cache: LRUCache[str, Any] | TTLCache[str, Any]
        if ttl is None:
            self._cache = LRUCache(maxsize=maxsize)
        else:
            self._cache = TTLCache(maxsize=maxsize, ttl=ttl)

    def get(self, key: str) -> Any | None:
        # cachetools 在 TTLCache 中过期 key 透明地 raise KeyError;用 .get 安全降级
        return self._cache.get(key)

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        # ttl 参数保留 Protocol 对称;实际生命周期由构造时 self._ttl 统一管控
        self._cache[key] = value

    def __len__(self) -> int:
        return len(self._cache)

    def clear(self) -> None:
        self._cache.clear()


__all__ = ["InMemoryLRUCache"]
