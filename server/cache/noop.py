"""NoopCache:Plan / LLM Response cache V1 占位(§4.8.1)。"""

from __future__ import annotations

from typing import Any


class NoopCache:
    """空实现 — 永不命中,永不存储。"""

    def get(self, key: str) -> Any | None:
        return None

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        return None


__all__ = ["NoopCache"]
