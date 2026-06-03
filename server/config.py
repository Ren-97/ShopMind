"""
ShopMind 集中配置(对应 docs/design.md §4.9)。

所有"魔法数字"、路径、模型名都集中在这里;业务代码 import 后只读使用,
绝不在业务代码里硬编码常量。

加载顺序:
  1. 进程启动时 python-dotenv 加载根目录 `.env`(若存在)
  2. 模块级常量从 os.getenv 读敏感字段(默认 None,缺失时业务层报错)
  3. 路径默认指向 `dataset/sample/`(样品间),通过 .env 覆盖切到全量
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# ─────────────────────────────────────────────────────────────
# .env 加载(在所有 getenv 之前)
# ─────────────────────────────────────────────────────────────
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _env(key: str, default: str) -> str:
    """读环境变量,带默认值。"""
    value = os.getenv(key)
    return value if value is not None and value != "" else default


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or raw == "":
        return default
    return int(raw)


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None or raw == "":
        return default
    return float(raw)


# ─────────────────────────────────────────────────────────────
# 模型选型(§4.2.1)
# ─────────────────────────────────────────────────────────────
LLM_AGENT_MODEL: str = _env("LLM_AGENT_MODEL", "claude-sonnet-4-6")  # 主对话 + 离线 caveats
LLM_FAST_MODEL: str = _env("LLM_FAST_MODEL", "claude-haiku-4-5")  # Planner + Reranker
EMBEDDING_PROVIDER: str = _env("EMBEDDING_PROVIDER", "gemini")  # "gemini" | "openai"
GEMINI_EMBEDDING_MODEL: str = _env("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")
OPENAI_EMBEDDING_MODEL: str = _env("OPENAI_EMBEDDING_MODEL", "text-embedding-3-large")
EMBEDDING_DIMENSION: int = _env_int("EMBEDDING_DIMENSION", 3072)
SPARSE_MODEL_NAME: str = _env("SPARSE_MODEL_NAME", "Qdrant/bm25")  # fastembed BM25

# ─────────────────────────────────────────────────────────────
# 检索(§4.1)
# ─────────────────────────────────────────────────────────────
RETRIEVAL_DENSE_LIMIT: int = _env_int("RETRIEVAL_DENSE_LIMIT", 20)
RETRIEVAL_SPARSE_LIMIT: int = _env_int("RETRIEVAL_SPARSE_LIMIT", 20)
RETRIEVAL_RRF_LIMIT: int = _env_int("RETRIEVAL_RRF_LIMIT", 30)
RETRIEVAL_PRODUCT_TOP_N: int = _env_int("RETRIEVAL_PRODUCT_TOP_N", 5)

# ─────────────────────────────────────────────────────────────
# Planner(§4.2)
# ─────────────────────────────────────────────────────────────
PLANNER_RECENT_TURNS: int = _env_int("PLANNER_RECENT_TURNS", 5)

# ─────────────────────────────────────────────────────────────
# Rerank + 阈值(§4.3)
# ─────────────────────────────────────────────────────────────
COARSE_THRESHOLD: float = _env_float("COARSE_THRESHOLD", 0.005)  # 粗排 RRF 阈值
RERANK_THRESHOLD: float = _env_float("RERANK_THRESHOLD", 0.5)  # LLM 自评分阈值
RERANK_TOP_N: int = _env_int("RERANK_TOP_N", 5)

# ─────────────────────────────────────────────────────────────
# Indexing(§4.5)
# ─────────────────────────────────────────────────────────────
CAVEATS_REVIEW_CHANGE_RATIO: float = _env_float("CAVEATS_REVIEW_CHANGE_RATIO", 0.20)
CAVEATS_MAX_AGE_DAYS: int = _env_int("CAVEATS_MAX_AGE_DAYS", 30)
# Review 规则过滤(§4.5.2 Step 1)
REVIEW_MIN_LENGTH: int = _env_int("REVIEW_MIN_LENGTH", 20)
REVIEW_DUP_CHAR_RATIO_MAX: float = _env_float("REVIEW_DUP_CHAR_RATIO_MAX", 0.5)
# 嵌入批大小:Gemini 单次硬上限 100,OpenAI 2048;统一保守 64
EMBEDDING_BATCH_SIZE: int = _env_int("EMBEDDING_BATCH_SIZE", 64)
# Caveats LLM 输出长度上限(§4.5.3 / §4.5.4)
CAVEATS_TEXT_MAX_CHARS: int = _env_int("CAVEATS_TEXT_MAX_CHARS", 200)
# matched_chunk 文本喂给 LLM(rerank + Agent payload)前的截断长度
MATCHED_CHUNK_TEXT_MAX_CHARS: int = _env_int("MATCHED_CHUNK_TEXT_MAX_CHARS", 300)
# Caveats 输入 reviews 过滤阈值(§4.5.4):rating ≤ 3 或 sentiment < 阈值 才视为可能含负面信号
CAVEATS_NEGATIVE_SENTIMENT_THRESHOLD: float = _env_float(
    "CAVEATS_NEGATIVE_SENTIMENT_THRESHOLD", -0.2
)
CAVEATS_NEGATIVE_RATING_MAX: int = _env_int("CAVEATS_NEGATIVE_RATING_MAX", 3)

# ─────────────────────────────────────────────────────────────
# Agent(§4.6)
# ─────────────────────────────────────────────────────────────
MAX_AGENT_TURNS: int = _env_int("MAX_AGENT_TURNS", 5)
THINKING_BUDGET_TOKENS: int = _env_int("THINKING_BUDGET_TOKENS", 0)
AGENT_MAX_TOKENS: int = _env_int("AGENT_MAX_TOKENS", 4096)
AGENT_RECENT_TURNS: int = _env_int("AGENT_RECENT_TURNS", 5)  # 主对话看的最近轮数
COMPARE_MIN_ITEMS: int = _env_int("COMPARE_MIN_ITEMS", 2)
COMPARE_MAX_ITEMS: int = _env_int("COMPARE_MAX_ITEMS", 5)
RECALL_HISTORY_TOP_N: int = _env_int("RECALL_HISTORY_TOP_N", 5)
SUGGESTIONS_MIN_COUNT: int = _env_int("SUGGESTIONS_MIN_COUNT", 3)
SUGGESTIONS_MAX_COUNT: int = _env_int("SUGGESTIONS_MAX_COUNT", 5)

# ─────────────────────────────────────────────────────────────
# Cache(§4.8)
# ─────────────────────────────────────────────────────────────
EMBEDDING_CACHE_SIZE: int = _env_int("EMBEDDING_CACHE_SIZE", 1000)
RETRIEVAL_CACHE_SIZE: int = _env_int("RETRIEVAL_CACHE_SIZE", 1000)
RETRIEVAL_CACHE_TTL_SECONDS: int = _env_int("RETRIEVAL_CACHE_TTL_SECONDS", 300)
CACHE_BACKEND: str = _env("CACHE_BACKEND", "memory")  # "memory" | "redis"(V2)
REDIS_URL: str = _env("REDIS_URL", "")  # V2 才填

# ─────────────────────────────────────────────────────────────
# 数据库(Postgres + asyncpg,需 `docker-compose up -d` 起)
# ─────────────────────────────────────────────────────────────
DATABASE_URL: str = _env(
    "DATABASE_URL",
    "postgresql+asyncpg://shopmind:shopmind@localhost:5432/shopmind",
)
TEST_DATABASE_URL: str = _env(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://shopmind:shopmind@localhost:5432/shopmind_test",
)

# ─────────────────────────────────────────────────────────────
# 基础设施 / 数据路径
# 设计偏离:默认指向 dataset/sample/(只读样品间),切全量改 .env
# ─────────────────────────────────────────────────────────────
QDRANT_PATH: str = _env("QDRANT_PATH", "./data/qdrant_storage/")
INGEST_DATASET_DIR: str = _env("INGEST_DATASET_DIR", "./dataset/sample/")
STATIC_FILES_DIR: str = _env("STATIC_FILES_DIR", "./dataset/sample/")

BASE_URL: str = _env("BASE_URL", "http://localhost:8000")

# Qdrant collection 名(嵌入式模式同一目录可多个 collection)
QDRANT_COLLECTION_NAME: str = _env("QDRANT_COLLECTION_NAME", "shopmind_products")

# ─────────────────────────────────────────────────────────────
# 多用户(§4.6.8)
# ─────────────────────────────────────────────────────────────
DEFAULT_USER_ID: str = _env("DEFAULT_USER_ID", "demo_user_1")
USER_ID_HEADER: str = "X-User-Id"

# ─────────────────────────────────────────────────────────────
# .env 加载敏感字段(§4.9 末尾)
# ─────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY: str | None = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY: str | None = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL: str | None = os.getenv("OPENAI_BASE_URL") or None
GEMINI_API_KEY: str | None = os.getenv("GEMINI_API_KEY")

# ─────────────────────────────────────────────────────────────
# Eval L3 Judge:Doubao via 火山引擎 Ark(OpenAI-compatible)
# 跨模型 judging:避免 Self-Enhancement Bias(Zheng et al. 2023)
# 没配 ARK_API_KEY → L3 优雅 skip,只跑 L1+L2
# ─────────────────────────────────────────────────────────────
ARK_API_KEY: str | None = os.getenv("ARK_API_KEY")
ARK_BASE_URL: str = _env("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3/")
DOUBAO_JUDGE_MODEL: str = _env("DOUBAO_JUDGE_MODEL", "ep-20260514111645-lmgt2")

# ─────────────────────────────────────────────────────────────
# 派生:绝对路径(避免业务层重复 resolve)
# ─────────────────────────────────────────────────────────────
def _abs(p: str) -> Path:
    """相对路径 → 项目根锚定的绝对路径。"""
    path = Path(p)
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


QDRANT_PATH_ABS: Path = _abs(QDRANT_PATH)
INGEST_DATASET_DIR_ABS: Path = _abs(INGEST_DATASET_DIR)
STATIC_FILES_DIR_ABS: Path = _abs(STATIC_FILES_DIR)


__all__ = [
    # 模型
    "LLM_AGENT_MODEL", "LLM_FAST_MODEL",
    "EMBEDDING_PROVIDER", "GEMINI_EMBEDDING_MODEL", "OPENAI_EMBEDDING_MODEL",
    "EMBEDDING_DIMENSION", "SPARSE_MODEL_NAME",
    # 检索
    "RETRIEVAL_DENSE_LIMIT", "RETRIEVAL_SPARSE_LIMIT", "RETRIEVAL_RRF_LIMIT",
    "RETRIEVAL_PRODUCT_TOP_N",
    # Planner
    "PLANNER_RECENT_TURNS",
    # Rerank
    "COARSE_THRESHOLD", "RERANK_THRESHOLD", "RERANK_TOP_N",
    # Indexing
    "CAVEATS_REVIEW_CHANGE_RATIO", "CAVEATS_MAX_AGE_DAYS",
    "REVIEW_MIN_LENGTH", "REVIEW_DUP_CHAR_RATIO_MAX",
    "EMBEDDING_BATCH_SIZE", "CAVEATS_TEXT_MAX_CHARS",
    "MATCHED_CHUNK_TEXT_MAX_CHARS",
    "CAVEATS_NEGATIVE_SENTIMENT_THRESHOLD", "CAVEATS_NEGATIVE_RATING_MAX",
    # Agent
    "MAX_AGENT_TURNS", "THINKING_BUDGET_TOKENS", "AGENT_MAX_TOKENS",
    "AGENT_RECENT_TURNS", "COMPARE_MIN_ITEMS", "COMPARE_MAX_ITEMS",
    "RECALL_HISTORY_TOP_N", "SUGGESTIONS_MIN_COUNT", "SUGGESTIONS_MAX_COUNT",
    # Cache
    "EMBEDDING_CACHE_SIZE", "RETRIEVAL_CACHE_SIZE", "RETRIEVAL_CACHE_TTL_SECONDS",
    "CACHE_BACKEND", "REDIS_URL",
    # 数据库
    "DATABASE_URL", "TEST_DATABASE_URL",
    # 路径
    "PROJECT_ROOT", "QDRANT_PATH", "INGEST_DATASET_DIR",
    "STATIC_FILES_DIR", "BASE_URL", "QDRANT_COLLECTION_NAME",
    "QDRANT_PATH_ABS", "INGEST_DATASET_DIR_ABS",
    "STATIC_FILES_DIR_ABS",
    # 多用户
    "DEFAULT_USER_ID", "USER_ID_HEADER",
    # Secrets
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL", "GEMINI_API_KEY",
    # Eval L3 Judge
    "ARK_API_KEY", "ARK_BASE_URL", "DOUBAO_JUDGE_MODEL",
]
