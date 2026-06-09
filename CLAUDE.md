# ShopMind — Claude Code 项目指南

> 每次新 Claude Code 会话进入项目时自动读取本文件。
> 完整设计在 `docs/design.md`,本文件是浓缩版指针。

---

## 1. 项目背景

**基于 RAG 的电商导购 AI Agent**(字节跳动竞赛)。

- **时间**:20 天单人 V1,AI 辅助开发
- **后端**:Python 3.10+ + FastAPI(async)+ SQLAlchemy v2 + Pydantic v2
- **前端**:**原生 Android**(Kotlin + Jetpack Compose,课题硬要求)
- **LLM**:Claude Sonnet 4.6(主对话)+ Claude Haiku 4.5(Planner / Reranker)
- **Embedding**:Gemini `gemini-embedding-001`(3072 维,默认)/ OpenAI `text-embedding-3-large`(可切)+ fastembed BM25 + jieba
- **Storage**:Postgres(catalog + user state,JSONB properties + GIN 索引,docker-compose 起本地)+ Qdrant 嵌入模式(向量索引)
- **加分方向**:4.1 业务闭环(购物车 / 下单)+ 4.3 对话智能(多轮 / 反选 / 对比)+ caveats 诚实推荐;**4.2 多模态不做**
- **核心要求**:**无幻觉**(课题减分第 1 条)

---

## 2. 必读章节(`docs/design.md`)

按任务找:

| 你要做 | 必读章节 |
|---|---|
| 任何 RAG 检索(4 策略 + Dispatcher) | §4.1 自适应检索 |
| Query Planner / Reranker / Agent 工具循环 | §4.3 + 附录 A(search 工具内部流程)|
| 数据库 schema | §5.1 Postgres(商品族 / 用户态 / ingest_manifest)|
| Ingest pipeline(chunking / caveats / per-field diff) | §5.3 |
| Agent + 7 工具 | §4.3 |
| SSE 协议 + 6 种 card schema | §4.6 |
| 缓存与降级 | §8.2 |
| **任何"魔法数字"或配置** | **§7 集中清单** |
| 测试 / eval | §8.1 |

---

## 3. 防幻觉三道铁律(违反直接 PR 拒绝)

| # | 规则 | 落地位置 |
|---|---|---|
| 1 | **SQL Hard Filter**:Repo 层永远带 `WHERE is_active=TRUE`,Filtered Semantic 策略用 hard_constraints 走 SQL 过滤,**不交给 LLM** | §4.2 铁律 1 + §5.1 |
| 2 | **Rerank + 阈值兜底**:候选 `max(score) < RERANK_THRESHOLD` → 返回空 → 触发 no_match 文案"没找到符合的",**不硬推不相关商品** | §4.2 铁律 2 + 附录 A step 6B |
| 3 | **Product facts 走 DB**:LLM 引用商品任何属性(价格 / 规格 / 成分 / 库存)**必从 tool 返回的 ProductSummary 字段**,**不读 matched_chunks 原文回答属性**,不读 chat_history 编造数字 | §4.2 铁律 3 + Agent system prompt |

**事实分层 + 来源标注**:
- L1 商家结构化字段 → 直接引用
- L2 商家描述 / FAQ → 标"根据商品介绍"
- L3 用户评论 → **必标**"根据用户评论 / 有用户反馈"(不可混淆为商家声明)

---

## 4. 编码约定

### Python(后端)
- **接口抽象**:Embedder / Reranker / RetrievalStrategy / Cache 全部 `typing.Protocol`
- **不抽 LLMClient**:Claude 是承诺单一 LLM,模块化组织即可
- **所有 Repo 方法第一参数 `user_id: str`**,SQL WHERE 永远带 user_id(§4.3 多用户隔离)
- **Tool 函数签名**:`user_id` 在后端注入,**不在 Claude tool schema 里暴露**
- **任何"魔法数字"进 `config.py`**,业务代码不写死(§7)
- **错误处理**:Tool 异常包装成 `{"error": "..."}` 回灌 Claude,SDK 自带 retry(`max_retries=2`)
- **日志**:`structlog` 结构化(planner_call / tool_execution_failed / ingest_failed 等关键事件)
- **Pydantic**:所有 IO 数据 / API 模型 / Tool 输入输出都用 BaseModel
- **async**:FastAPI 路由 + Anthropic streaming + Qdrant client 都 async

### Android(前端)
- Material 3 默认主题(`MaterialTheme(colorScheme = dynamicLightColorScheme(...))`)
- Compose Navigation 多页:`ChatScreen` / `ProductDetailScreen` / `CartScreen` / `OrderConfirmScreen` / `ProfileScreen`(§6.2)
- 图片用 Coil `AsyncImage`
- SSE 用 OkHttp `EventSource`
- `X-User-Id` header 走 OkHttp Interceptor 自动加(§4.3)
- 状态:ViewModel + StateFlow,Compose 响应式渲染

---

## 5. 绝对不要做

- ❌ **硬编码 API key**:走 `.env` + `os.getenv()`
- ❌ **LLM 直接读 chunks 原文回答商品属性**:违反防幻觉铁律 3
- ❌ **闲聊 query 进 Planner**:Agent 层先过滤("好的"/"谢谢" Agent 直接回,不调任何 tool)
- ❌ **手写 retry 循环**:Anthropic SDK 已内置 `max_retries=2`
- ❌ **构造"降级 plan"**:Planner Hard Fail → 直接返回兜底文案,不构造误导性结果
- ❌ **跨用户访问数据**:所有用户态查询带 `user_id` filter,严防越权
- ❌ **跳过 eval**:写完一个组件就跑一次 `tests/eval.py`,数据驱动调优
- ❌ **过度抽象未来不会发生的事**:V2 升级路径写在文档作 talking point,不在 V1 实施
- ❌ **在 SSE 流里推完整 product 数据**:Card 是 lean schema,详情走 `/product/{id}` REST(§4.6)
- ❌ **跨 SQL + Qdrant 写没考虑幂等**:SQL 用 UPSERT,Qdrant 用同 chunk_id 覆盖

---

## 6. 设计 vs 实施 — 何时回来讨论

| 情况 | 处理 |
|---|---|
| **跨组件的架构调整**(影响多个 §4/§5 子系统) | **停下,问用户** → 改 design.md → 再写代码 |
| **新加 / 删除工具** | 停下,问用户(影响 Agent prompt + UI + 课题加分项) |
| **改 SSE 协议字段** | 停下,问用户(前后端都要改) |
| **改防幻觉三道铁律** | 停下,问用户(课题第一减分项) |
| **改 user_profile schema** | 停下,问用户(影响多用户演示) |
| 单文件内部实现细节(变量名、私有函数拆分) | 自决 |
| 调 prompt 文案 | 自决,但**必须跑 eval 验证** |
| 调 config 数字(阈值 / limit) | 自决,**eval 驱动** |
| Android UI 组件内部布局细节 | 自决 |
| **任何不确定** | **问用户而不是猜** |

---

## 7. 跑命令

```bash
# 后端启动
uv sync
uv run uvicorn server.main:app --reload --port 8000

# Ingest(扫 JSON → Postgres → Qdrant,可重复跑,自动 per-field diff)
# 跑前先起 Postgres:`docker-compose up -d postgres`
python scripts/ingest.py

# Eval(跑 ground truth 15-20 case,出 markdown 报告)
python tests/eval.py

# Seed demo users(首次 / DB reset 后)
python scripts/seed_users.py

# Android(在 client/ShopMind/ 打开 Android Studio)
```

`.env` 必填:
```
ANTHROPIC_API_KEY=sk-ant-...
GEMINI_API_KEY=...           # 默认 Embedding provider
# OPENAI_API_KEY=sk-...      # 仅切到 OpenAI Embedding 时需要
# ARK_API_KEY=...            # 仅 eval L3 Judge(豆包)需要
```

---

## 8. 配置项速查(完整在 `server/config.py`,§7)

| 类别 | 关键配置 |
|---|---|
| 模型 | `LLM_AGENT_MODEL` / `LLM_FAST_MODEL` / `EMBEDDING_PROVIDER`(默认 gemini)|
| 检索候选池 | `RETRIEVAL_DENSE_LIMIT=20` / `RETRIEVAL_SPARSE_LIMIT=20` / `RETRIEVAL_RRF_LIMIT=30` / `RETRIEVAL_PRODUCT_TOP_N=5` |
| Planner | `PLANNER_RECENT_TURNS=5` |
| Rerank | `RERANK_TOP_N=4` / `COARSE_THRESHOLD=0.005` / `RERANK_THRESHOLD=0.5` |
| Caveats | `CAVEATS_REVIEW_CHANGE_RATIO=0.20` / `CAVEATS_MAX_AGE_DAYS=30` |
| Agent | `MAX_AGENT_TURNS=5` / `THINKING_BUDGET_TOKENS=0`(V1 禁用)|
| Cache | `EMBEDDING_CACHE_SIZE=1000` / `RETRIEVAL_CACHE_SIZE=1000` / `RETRIEVAL_CACHE_TTL_SECONDS=300` |
| 路径 | `DATABASE_URL`(Postgres)/ `QDRANT_PATH` / `INGEST_DATASET_DIR` |
| 多用户 | `X-User-Id` header 默认 `"demo_user_1"`(无 auth,§4.3)|

**任何写死的数字都该来自 config.py**,不写在业务代码里。

---

## 9. 项目结构速查(完整在 design.md §6)

```
server/
├── api/             FastAPI 路由(SSE + REST)
├── agent/           Agent 编排 + tool loop
├── tools/           7 个工具(§4.3)
├── llm/             Claude 调用 + Planner + Reranker + prompts/
├── rag/
│   ├── retrieval/   Dispatcher + 4 strategies
│   ├── embedders/   Embedder Protocol + Gemini / OpenAI / 带缓存包装
│   ├── sparse/      fastembed BM25 + jieba
│   └── reranking/   LLM Reranker
├── indexing/        ingest.py + chunking 策略类
├── storage/         catalog_repo / user_repo / manifest_repo / vector_index
├── cache/           Cache Protocol + InMemoryLRUCache / NoopCache
├── domain/          Pydantic 共享类型
├── config.py
└── main.py

scripts/   ingest.py / seed_users.py / reset_demo.py / chat_cli.py / diag_search.py
data/      qdrant_storage/
tests/     eval.py / eval_cases.json + 各 chunk 冒烟测试
docs/      design.md(单一设计真相源)
dataset/   ecommerce_agent_dataset/(全量,默认)
client/    Android 原生项目
```

---

## 10. 答辩准备(贯穿开发)

写代码时心里有数,几个高频问题预设答案:

- **"为什么用 Adaptive Retrieval 而不是单一 Hybrid?"** → §4.1 答辩点
- **"为什么 V1 用 LLM Rerank 而不是 Cross-Encoder?"** → §9.2 V2 演进
- **"怎么防幻觉?"** → 三道铁律(§4.2)
- **"多用户怎么实现的?"** → §4.3 X-User-Id 注入 + Schema 多用户
- **"V2 怎么扩展?"** → 接口预留(Embedder / Reranker / Cache),业务代码零改动(§9.2)

---

**完成 V1 demo + Eval report + 答辩准备 = 项目成功**。

把 design.md 当单一真相源,但**记住:设计是活的,实施过程中发现更好做法 → 回讨论 → 改文档 → 再实施**。
