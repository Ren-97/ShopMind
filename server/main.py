"""
FastAPI 应用入口(对应 docs/design.md §6 + §4.6.8 + §4.7)。

Chunk 1 范围:
- FastAPI app 骨架 + CORS
- Lifespan:启动期 create_all (SQLite) + ensure_collection (Qdrant)
- /static 挂载到 STATIC_FILES_DIR(图片 image_url 拼接)
- /health 健康检查 + /readyz 含 Qdrant 状态
- 路由模块(api/chat.py 等)Chunk 2+ 加入,这里先留 import 注释

启动:`uv run uvicorn server.main:app --reload --port 8000`
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import structlog
from fastapi import FastAPI, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from server import config
from server.storage.db import create_all, dispose
from server.storage.vector_index import get_vector_index


# ─────────────────────────────────────────────────────────────
# 日志:structlog 结构化(planner_call / tool_execution_failed 等)
# ─────────────────────────────────────────────────────────────
logging.basicConfig(format="%(message)s", level=logging.INFO)
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)
log = structlog.get_logger("shopmind")


# ─────────────────────────────────────────────────────────────
# Lifespan:启动 / 关闭钩子
# ─────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    log.info(
        "startup_begin",
        sqlite=str(config.SQLITE_PATH_ABS),
        qdrant=str(config.QDRANT_PATH_ABS),
        static=str(config.STATIC_FILES_DIR_ABS),
        dataset=str(config.INGEST_DATASET_DIR_ABS),
    )
    # 1. SQLite 建表(幂等)
    await create_all()

    # 2. Qdrant collection 初始化(幂等)
    vector_index = get_vector_index()
    try:
        await vector_index.ensure_collection()
        log.info("qdrant_ready", collection=vector_index.collection_name)
    except Exception as e:
        # Qdrant 初始化失败不阻塞 app 启动(可能首次 ingest 前文件夹为空,日志告警即可)
        log.warning("qdrant_init_warning", error=str(e))

    log.info("startup_complete")
    yield

    # ─── 关闭 ───
    log.info("shutdown_begin")
    try:
        await vector_index.close()
    except Exception as e:
        log.warning("qdrant_close_warning", error=str(e))
    await dispose()
    log.info("shutdown_complete")


# ─────────────────────────────────────────────────────────────
# FastAPI app
# ─────────────────────────────────────────────────────────────
app = FastAPI(
    title="ShopMind API",
    version="0.1.0",
    description="RAG 多模态电商导购 AI Agent",
    lifespan=lifespan,
)

# CORS:Android 客户端 + 本地调试
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# /static 挂载:商品图片相对路径(image_path)拼出 image_url
# 注意:STATIC_FILES_DIR_ABS 必须存在,首跑确保目录有内容
if config.STATIC_FILES_DIR_ABS.exists():
    app.mount(
        "/static",
        StaticFiles(directory=str(config.STATIC_FILES_DIR_ABS)),
        name="static",
    )
else:
    log.warning(
        "static_dir_missing",
        path=str(config.STATIC_FILES_DIR_ABS),
        hint="dataset/sample/ 或 .env 配置的 STATIC_FILES_DIR 不存在",
    )


# ─────────────────────────────────────────────────────────────
# 基础端点
# ─────────────────────────────────────────────────────────────
class HealthResponse(BaseModel):
    status: str = Field(..., examples=["ok"])
    version: str


class ReadyResponse(BaseModel):
    status: str
    sqlite: bool
    qdrant: bool
    qdrant_points: int | None = None


@app.get("/health", response_model=HealthResponse, tags=["meta"])
async def health() -> HealthResponse:
    return HealthResponse(status="ok", version=app.version)


@app.get("/readyz", response_model=ReadyResponse, tags=["meta"])
async def readyz() -> ReadyResponse:
    """完整可服务检查:SQLite 可建表 + Qdrant collection 存在 + count 可读。"""
    sqlite_ok = config.SQLITE_PATH_ABS.parent.exists()
    qdrant_ok = False
    qdrant_points: int | None = None
    try:
        vector_index = get_vector_index()
        qdrant_points = await vector_index.count_points()
        qdrant_ok = True
    except Exception as e:
        log.warning("readyz_qdrant_error", error=str(e))

    return ReadyResponse(
        status="ok" if (sqlite_ok and qdrant_ok) else "degraded",
        sqlite=sqlite_ok,
        qdrant=qdrant_ok,
        qdrant_points=qdrant_points,
    )


@app.get("/whoami", tags=["meta"])
async def whoami(x_user_id: str = Header(default=config.DEFAULT_USER_ID)) -> dict[str, str]:
    """V1 无 auth,从 X-User-Id header 取当前用户(§4.6.8)。"""
    return {"user_id": x_user_id}


# ─────────────────────────────────────────────────────────────
# 业务路由(Chunk 2+ 加入)
# ─────────────────────────────────────────────────────────────
# from server.api import chat, cart, product, order, profile
# app.include_router(chat.router)
# app.include_router(cart.router)
# app.include_router(product.router)
# app.include_router(order.router)
# app.include_router(profile.router)
