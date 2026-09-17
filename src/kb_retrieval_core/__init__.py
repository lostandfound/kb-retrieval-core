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

__all__ = [
    "Claim", "Chunk", "Entity", "Evidence", "Reference", "Relation", "SearchHit", "Snapshot",
    "SnapshotLoadError", "load", "load_snapshot", "normalize_json", "normalize_source_id",
    "chunk_entity", "chunk_snapshot", "chunks_for_snapshot", "chunk_to_dict",
    "canonical_chunk_jsonl", "canonical_chunk_bytes", "canonical_chunk_hash", "chunk_jsonl_bytes", "chunks_hash",
    "LexicalIndex", "build_lexical_index", "character_ngrams", "normalize_text",
    "CONFIDENCE_WEIGHTS", "GraphIndex", "GraphSearcher", "build_graph_index",
    "ContextAssemblyError", "ContextReference", "ContextReport", "EvidencePacket", "assemble_context", "assemble_evidence", "build_context",
    "EvaluationAggregate", "EvaluationCase", "EvaluationError", "EvaluationLoadError", "EvaluationReport", "EvaluationResult", "RetrievedResult", "evaluate", "load_evaluation_cases",
    "SQLITE_INDEX_FORMAT", "SQLiteIndex", "PersistentSQLiteIndex", "PersistentIndex", "SQLiteIndexError", "build_sqlite_index", "open_sqlite_index",
]

__version__ = "0.1.0"
