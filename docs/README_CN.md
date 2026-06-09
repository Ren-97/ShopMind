# ShopMind

[English](../README.md) · **中文**

> 基于 RAG 的电商导购 AI Agent —— 对话式理解需求，答案锚定真实商品库，**诚实、不编造、可闭环**。

用户说一句话（"1000 以内的运动鞋""帮我对比这两款""不要 XX 牌"），ShopMind 理解意图、从真实商品库检索、给出**有依据**的推荐，并支持在对话里直接加购、下单。核心约束是**无幻觉**——架构里的每一处取舍（SQL 硬过滤、重排阈值兜底、商品事实只走 DB）都为它服务。

设计细节（为什么这么设计、检索链路、防幻觉解法）见 **[design.md](design.md)** —— 单一设计真相源，开发前先通读。本文只讲**上手部署**。

---

## 核心特性

- **自适应检索**：Planner 把查询分诊到 4 种策略（纯 SQL / ID 直取 / 硬过滤语义 / 纯语义），每类走最优路径，而非单一 Hybrid 一刀切。
- **三道防幻觉铁律**：SQL 硬过滤 + 重排阈值兜底（没找到就说"没找到"，不硬凑）+ 商品事实只引用 DB 字段。
- **对话智能**：多轮上下文、反选（排除品牌 / 价格上限）、商品对比、个性化软重排。
- **业务闭环**：对话内加购 → 下单结算 → 订单确认，全程卡片化。
- **诚实评论摘要**：离线对评论做双面平衡摘要，优点与注意点都给（差评分层采样保底）。
- **SSE 流式**：思考过程、文本、卡片、追问建议边生成边推送。

## 技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python 3.10+ · FastAPI（async）· SQLAlchemy v2 · Pydantic v2 · structlog |
| 前端 | 原生 Android（Kotlin + Jetpack Compose · Navigation · Coil · OkHttp SSE） |
| LLM | Claude Sonnet 4.6（主对话 + 评论摘要）· Claude Haiku 4.5（Planner / Reranker） |
| Embedding | Gemini `gemini-embedding-001`（3072 维，默认）/ OpenAI（可切）· fastembed BM25 + jieba |
| 存储 | Postgres（商品 + 用户态，JSONB + GIN）· Qdrant 嵌入式（向量索引，落盘 `data/`） |

---

## 环境要求

- **Python 3.10+** 与 [uv](https://docs.astral.sh/uv/)（依赖与运行）
- **Docker**（起本地 Postgres）
- **Android Studio**（编译运行客户端，可选）

## 首次部署

```powershell
# 0. 装依赖
uv sync

# 1. 配 .env：复制模板并填入 API Key
cp .env.example .env        # 至少填 ANTHROPIC_API_KEY 和 GEMINI_API_KEY

# 2. 起本地 Postgres（首次起容器会自动跑 init SQL 建库）
docker compose up -d postgres

# 3. 导入商品 catalog → Postgres，并生成向量 → Qdrant（可重复跑，自动 per-field 增量）
uv run python scripts/ingest.py

# 4. 初始化 demo 用户（必跑；DB reset 后也要重跑）
uv run python scripts/seed_users.py

# 5. 起后端
uv run uvicorn server.main:app --reload --port 8000
```

后端起在 `http://localhost:8000`。

### `.env` 必填项

| 变量 | 用途 | 必填 |
|---|---|---|
| `ANTHROPIC_API_KEY` | Claude（对话 / 规划 / 重排 / 摘要） | ✅ |
| `GEMINI_API_KEY` | Gemini Embedding（默认 provider） | ✅ |
| `OPENAI_API_KEY` | 切到 OpenAI Embedding 时才需 | 可选 |
| `ARK_API_KEY` | 火山引擎 Doubao —— Eval L3 Judge（缺则 L3 跳过） | 可选 |

数据库默认值（`DATABASE_URL`）与上面的 docker-compose 一致，无需改动。数据集默认指向全量 `dataset/ecommerce_agent_dataset/`，换数据集改 `.env` 里的 `INGEST_DATASET_DIR` / `STATIC_FILES_DIR`。

## 常用命令

```powershell
# 清 demo 业务数据（购物车 / 订单 / 历史）+ 覆盖重置 demo profile
uv run python scripts/reset_demo.py

# 命令行调试对话（需后端已启动；不开 Android 也能跑通链路）
uv run python scripts/chat_cli.py

# Eval：ground truth case 分层评测，出报告
uv run python tests/eval.py        # 加 --layers 1 只跑 Planner 层，省 token
```

> **关于 Eval**：当前仓库里的 eval 报告**不是最终测试结果**，其中暴露的问题**已全部修复**。Eval 烧 token，按需在改了 prompt / config 后跑。

## Android 客户端

用 Android Studio 打开 `client/ShopMind/`，编译运行。客户端通过 `X-User-Id` header 标识用户（OkHttp 拦截器自动加），SSE 接收流式响应并渲染卡片。模拟器访问宿主机后端用 `10.0.2.2:8000`（见 `.env` 的 `BASE_URL`）。

---

## 项目结构

```
server/      FastAPI 后端：api / agent / tools / llm / rag / indexing / storage / cache / domain
client/      Android 原生项目（Kotlin + Compose）
scripts/     ingest / seed_users / reset_demo / chat_cli / diag_search
tests/       eval 框架 + 各 chunk 冒烟测试
dataset/     ecommerce_agent_dataset/（全量，默认）
data/        qdrant_storage/（向量落盘）
docs/        design.md（单一设计真相源）
```

完整目录说明见 [design.md §6](design.md)。
