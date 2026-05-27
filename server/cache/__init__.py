"""Cache 子系统(§4.8)。

V1 实现:
- `InMemoryLRUCache`:cachetools 包装,可配 maxsize + 全局 TTL
- `NoopCache`:no-op,占位用(Plan / LLM Response cache V1 不开)

V2 升级:加 `RedisCache(redis_url)`,业务代码不动(只换 factory)。

接口为 sync(Cache Protocol 用 `def`,非 `async def`):
- in-memory dict 操作纳秒级,async 反而引入开销
- 业务调用方(async)直接调 sync get/set 无需 await
"""

from server.cache.in_memory import InMemoryLRUCache
from server.cache.noop import NoopCache
from server.cache.protocol import Cache

__all__ = ["Cache", "InMemoryLRUCache", "NoopCache"]
