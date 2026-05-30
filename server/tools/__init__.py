"""Agent Tools 子系统(§4.6.1 — 7 个工具)。

每个 tool 文件导出一个 `Tool` 实例;`build_tool_registry()` 在 orchestrator 启动
时一次性聚合,Anthropic API 直接吃 `tool.input_schema`。

`user_id` 由 backend 注入,**不在** Claude 看到的 tool schema 里出现(§4.6.8 F5)。
"""

from server.tools.base import (
    AgentDeps,
    Tool,
    ToolError,
    ToolResult,
    build_tool_registry,
    execute_tool,
    tool_specs_for_anthropic,
)

__all__ = [
    "AgentDeps",
    "Tool",
    "ToolError",
    "ToolResult",
    "build_tool_registry",
    "execute_tool",
    "tool_specs_for_anthropic",
]
