"""LLM prompt 文案集中地(§4.2.5 / §4.3)。

每个 prompt 一个文件,与代码一起 version control。System prompt 单独导出
便于在 anthropic Tool Use 调用时作 prompt cache 单元(`cache_control` ephemeral)。
"""
