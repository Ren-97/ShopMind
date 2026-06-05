"""LLM 调用模块(§4.2.2)。

V1 不抽 LLMClient 接口(承诺单一 Claude 家族),按用途分文件组织:
- review_sentiment.py — Haiku 每条 review 抽情感(§4.5.2)
- review_summarizer.py — Sonnet 离线抽评论平衡摘要 highlights + caveats(§4.5.4)
- (chunk3+) agent.py / planner.py / reranker.py
"""
