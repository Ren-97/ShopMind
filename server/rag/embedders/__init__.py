"""Dense Embedder 抽象 + 默认实现(§4.5.2 Step 4)。"""

from server.rag.embedders.protocol import Embedder
from server.rag.embedders.openai_embedder import OpenAIEmbedder

__all__ = ["Embedder", "OpenAIEmbedder"]
