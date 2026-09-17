"""Domain-independent retrieval primitives for knowledge bases."""

from .models import Claim, Chunk, Entity, Evidence, Reference, Relation, SearchHit, Snapshot
from .snapshot import SnapshotLoadError, load, load_snapshot, normalize_json, normalize_source_id
from .chunking import (
    canonical_chunk_bytes,
    canonical_chunk_hash,
    canonical_chunk_jsonl,
    chunk_entity,
    chunk_jsonl_bytes,
    chunk_snapshot,
    chunks_for_snapshot,
    chunks_hash,
    chunk_to_dict,
)
from .lexical import LexicalIndex, build_lexical_index, character_ngrams, normalize_text
from .embeddings import (
    DeterministicTestEmbedder,
    DocumentEmbeddingCallable,
    Embedder,
    EmbeddedDocument,
    EmbeddingConfig,
    EmbeddingDocument,
    EmbeddingProviderError,
    InjectedEmbedder,
    QueryEmbeddingCallable,
    VectorIndexConfig,
    embedding_config_to_dict,
    embedding_fingerprint,
    validate_document_embeddings,
    validate_query_embedding,
    vector_index_config_to_dict,
    vector_index_fingerprint,
)
from .graph_search import CONFIDENCE_WEIGHTS, GraphIndex, GraphSearcher, build_graph_index
from .context import ContextAssemblyError, ContextReference, ContextReport, EvidencePacket, assemble_context, assemble_evidence, build_context
from .evaluation import EvaluationAggregate, EvaluationCase, EvaluationError, EvaluationLoadError, EvaluationReport, EvaluationResult, RetrievedResult, evaluate, load_evaluation_cases
from .sqlite_index import (
    SQLITE_INDEX_FORMAT,
    PersistentIndex,
    PersistentSQLiteIndex,
    SQLiteIndex,
    SQLiteIndexError,
    build_sqlite_index,
    open_sqlite_index,
)
from .vector_store import (
    VECTOR_SIDECAR_FORMAT,
    SQLiteVectorSidecar,
    VectorRecord,
    VectorSidecarError,
    canonical_vector_bytes,
)
from .vector_retrieval import (
    VectorRetrievalError,
    VectorRetriever,
    build_vector_retriever,
)
from .rrf import FusionError, RRFConfig, fuse_entity_rankings

__all__ = [
    "Claim", "Chunk", "Entity", "Evidence", "Reference", "Relation", "SearchHit", "Snapshot",
    "SnapshotLoadError", "load", "load_snapshot", "normalize_json", "normalize_source_id",
    "chunk_entity", "chunk_snapshot", "chunks_for_snapshot", "chunk_to_dict",
    "canonical_chunk_jsonl", "canonical_chunk_bytes", "canonical_chunk_hash", "chunk_jsonl_bytes", "chunks_hash",
    "LexicalIndex", "build_lexical_index", "character_ngrams", "normalize_text",
    "DeterministicTestEmbedder", "DocumentEmbeddingCallable", "Embedder", "EmbeddedDocument",
    "EmbeddingConfig", "EmbeddingDocument", "EmbeddingProviderError", "InjectedEmbedder",
    "QueryEmbeddingCallable", "VectorIndexConfig",
    "embedding_config_to_dict", "embedding_fingerprint", "validate_document_embeddings",
    "validate_query_embedding", "vector_index_config_to_dict", "vector_index_fingerprint",
    "CONFIDENCE_WEIGHTS", "GraphIndex", "GraphSearcher", "build_graph_index",
    "ContextAssemblyError", "ContextReference", "ContextReport", "EvidencePacket", "assemble_context", "assemble_evidence", "build_context",
    "EvaluationAggregate", "EvaluationCase", "EvaluationError", "EvaluationLoadError", "EvaluationReport", "EvaluationResult", "RetrievedResult", "evaluate", "load_evaluation_cases",
    "SQLITE_INDEX_FORMAT", "SQLiteIndex", "PersistentSQLiteIndex", "PersistentIndex", "SQLiteIndexError", "build_sqlite_index", "open_sqlite_index",
    "VECTOR_SIDECAR_FORMAT", "SQLiteVectorSidecar", "VectorRecord", "VectorSidecarError",
    "canonical_vector_bytes",
    "VectorRetrievalError", "VectorRetriever", "build_vector_retriever",
    "FusionError", "RRFConfig", "fuse_entity_rankings",
]

__version__ = "0.1.0"
