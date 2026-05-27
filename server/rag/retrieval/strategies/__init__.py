"""4 种检索策略(§4.1.3)+ base.py Protocol。"""

from server.rag.retrieval.strategies.base import RetrievalStrategy
from server.rag.retrieval.strategies.filtered_semantic import FilteredSemanticStrategy
from server.rag.retrieval.strategies.id_lookup import IDLookupStrategy
from server.rag.retrieval.strategies.pure_semantic import PureSemanticStrategy
from server.rag.retrieval.strategies.structured import StructuredStrategy

__all__ = [
    "RetrievalStrategy",
    "StructuredStrategy",
    "IDLookupStrategy",
    "FilteredSemanticStrategy",
    "PureSemanticStrategy",
]
