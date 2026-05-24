# ShopMind

基于 RAG 的多模态电商智能导购 AI Agent。

## 设计文档

- **设计方案与功能清单**:[docs/design.md](docs/design.md) — 开发前先通读
- **端到端架构图**:[docs/architecture.svg](docs/architecture.svg) — 7 步 RAG pipeline + 三道防幻觉防线
- **课题原文**:`topic_info/课题说明会*.pdf`

## 本地环境（uv）

需要 Python 3.10+ 与 [uv](https://docs.astral.sh/uv/)。

```powershell
cd ShopMind
uv sync
```

`uv` 不在 PATH 时用：`python -m uv sync`

复制 `.env.example` 为 `.env` 并填入 API Key（接入 LLM 时使用）。
