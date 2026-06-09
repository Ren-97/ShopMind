# ShopMind

**English** · [中文](docs/README_CN.md)

> A RAG-based e-commerce shopping-guide AI agent — understands needs through conversation and grounds every answer in a real product catalog: **honest, no fabrication, end-to-end**.

Say it in one sentence ("running shoes under 1000", "compare these two", "no XX brand") and ShopMind figures out the intent, retrieves from a real product catalog, and gives **grounded** recommendations — with add-to-cart and checkout right inside the conversation. The core constraint is **zero hallucination**: every trade-off in the architecture (SQL hard-filtering, rerank threshold fallback, product facts sourced only from the DB) serves it.

For design details (why it's built this way, the retrieval pipeline, the anti-hallucination approach) see **[docs/design.md](docs/design.md)** (in Chinese) — the single source of design truth, read it first. This file covers **getting it running** only.

---

## Highlights

- **Adaptive retrieval**: the Planner triages each query into 4 strategies (pure SQL / ID lookup / filtered semantic / pure semantic) and routes each down its optimal path, rather than a one-size-fits-all hybrid search.
- **Three anti-hallucination iron rules**: SQL hard-filtering + rerank threshold fallback (says "nothing found" instead of forcing a poor match) + product facts cited only from DB fields.
- **Conversational intelligence**: multi-turn context, negation (exclude brands / price ceiling), product comparison, personalized soft reranking.
- **Closed business loop**: add-to-cart → checkout → order confirmation in conversation, all rendered as cards.
- **Honest review summaries**: offline two-sided balanced summaries of reviews — both upsides and caveats (stratified sampling guarantees negative reviews are seen).
- **SSE streaming**: thinking, text, cards, and follow-up suggestions are pushed as they are generated.

## Tech stack

| Layer | Choice |
|---|---|
| Backend | Python 3.10+ · FastAPI (async) · SQLAlchemy v2 · Pydantic v2 · structlog |
| Frontend | Native Android (Kotlin + Jetpack Compose · Navigation · Coil · OkHttp SSE) |
| LLM | Claude Sonnet 4.6 (main dialogue + review summaries) · Claude Haiku 4.5 (Planner / Reranker) |
| Embedding | Gemini `gemini-embedding-001` (3072-dim, default) / OpenAI (switchable) · fastembed BM25 + jieba |
| Storage | Postgres (catalog + user state, JSONB + GIN) · Qdrant embedded (vector index, persisted to `data/`) |

---

## Requirements

- **Python 3.10+** and [uv](https://docs.astral.sh/uv/) (dependencies & running)
- **Docker** (local Postgres)
- **Android Studio** (to build & run the client, optional)

## First-time setup

```powershell
# 0. Install dependencies
uv sync

# 1. Configure .env: copy the template and fill in your API keys
cp .env.example .env        # at minimum ANTHROPIC_API_KEY and GEMINI_API_KEY

# 2. Start local Postgres (init SQL runs automatically on first container start)
docker compose up -d postgres

# 3. Ingest catalog → Postgres, build vectors → Qdrant (re-runnable, per-field incremental)
uv run python scripts/ingest.py

# 4. Seed demo users (required; re-run after any DB reset)
uv run python scripts/seed_users.py

# 5. Start the backend
uv run uvicorn server.main:app --reload --port 8000
```

The backend runs at `http://localhost:8000`.

### Required `.env` keys

| Variable | Purpose | Required |
|---|---|---|
| `ANTHROPIC_API_KEY` | Claude (dialogue / planning / reranking / summaries) | ✅ |
| `GEMINI_API_KEY` | Gemini Embedding (default provider) | ✅ |
| `OPENAI_API_KEY` | Only when switching to OpenAI Embedding | Optional |
| `ARK_API_KEY` | Volcengine Doubao — Eval L3 Judge (L3 skipped if absent) | Optional |

The default `DATABASE_URL` matches the docker-compose above — no change needed. The dataset defaults to the full `dataset/ecommerce_agent_dataset/`; switch datasets via `INGEST_DATASET_DIR` / `STATIC_FILES_DIR` in `.env`.

## Common commands

```powershell
# Reset demo business data (cart / orders / history) + restore demo profiles
uv run python scripts/reset_demo.py

# Terminal chat client (needs the backend running; exercises the full pipeline without Android)
uv run python scripts/chat_cli.py

# Eval: layered evaluation over ground-truth cases, produces a report
uv run python tests/eval.py        # add --layers 1 to run only the Planner layer and save tokens
```

> **About Eval**: the eval report currently in the repo is **not the final result** — the issues it surfaced have **all been fixed**. Eval burns tokens; run it on demand after changing prompts / config.

## Android client

Open `client/ShopMind/` in Android Studio, then build & run. The client identifies the user via the `X-User-Id` header (added automatically by an OkHttp interceptor) and renders streamed SSE responses as cards. From an emulator, reach the host backend at `10.0.2.2:8000` (see `BASE_URL` in `.env`).

---

## Project layout

```
server/      FastAPI backend: api / agent / tools / llm / rag / indexing / storage / cache / domain
client/      Native Android project (Kotlin + Compose)
scripts/     ingest / seed_users / reset_demo / chat_cli / diag_search
tests/       eval framework + per-chunk smoke tests
dataset/     ecommerce_agent_dataset/ (full, default)
data/        qdrant_storage/ (vector persistence)
docs/        design.md (single source of design truth)
```

See [docs/design.md §6](docs/design.md) for the full layout.
