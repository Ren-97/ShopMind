"""FastAPI 路由层(§4.7 SSE + REST)。

模块:
- deps:get_current_user / get_orchestrator(单例)
- sse:AgentEvent → SSE wire 序列化
- chat:POST /chat(SSE,接 Orchestrator)
- cart / product / order / profile:REST CRUD
"""
