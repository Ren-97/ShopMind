"""共享 AsyncAnthropic 单例(§4.2.2 + CLAUDE.md §4)。

- 不抽 LLMClient 接口(承诺单一 Claude 家族)
- SDK 自带 max_retries=2,业务层不写重试循环
- 单例懒加载,避免 import 时校验 key
"""

from __future__ import annotations

from anthropic import AsyncAnthropic

from server import config

_client: AsyncAnthropic | None = None


def get_anthropic_client() -> AsyncAnthropic:
    """复用单一 AsyncAnthropic;首次调用校验 key。"""
    global _client
    if _client is None:
        if not config.ANTHROPIC_API_KEY:
            raise RuntimeError(
                "ANTHROPIC_API_KEY 未配置。在项目根目录 .env 里填入 ANTHROPIC_API_KEY。"
            )
        _client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY, max_retries=2)
    return _client


__all__ = ["get_anthropic_client"]
