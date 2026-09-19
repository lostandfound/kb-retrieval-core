"""Stable diagnostic codes for serialized failure envelopes.

Every package error derives from a builtin such as ``ValueError``, so a
consumer that only sees ``str(exc)`` has to match on message text. These codes
give each failure a machine-readable identity that survives message rewording.

Codes are part of the CLI error envelope contract: new codes may be added in a
minor release, but an existing code never changes meaning.
"""

from __future__ import annotations

from .context import ContextAssemblyError
from .embeddings import EmbeddingProviderError
from .evaluation import EvaluationError, EvaluationLoadError
from .retrieval import RetrievalError
from .rrf import FusionError
from .snapshot import SnapshotLoadError
from .sqlite_index import SQLiteIndexError
from .vector_retrieval import VectorRetrievalError
from .vector_store import VectorSidecarError


class UsageError(ValueError):
    """Invalid command-line usage, reported before any command runs."""


# Ordered by specificity is unnecessary: lookup walks the MRO, so a subclass
# always resolves to its own entry before a base class entry.
DIAGNOSTIC_CODES: dict[type[BaseException], str] = {
    UsageError: "usage_error",
    SnapshotLoadError: "snapshot_load_error",
    SQLiteIndexError: "sqlite_index_error",
    VectorSidecarError: "vector_sidecar_error",
    VectorRetrievalError: "vector_retrieval_error",
    RetrievalError: "retrieval_error",
    FusionError: "fusion_error",
    ContextAssemblyError: "context_assembly_error",
    EvaluationLoadError: "evaluation_load_error",
    EvaluationError: "evaluation_error",
    EmbeddingProviderError: "embedding_provider_error",
}

_INVALID_INPUT = "invalid_input"
_IO_ERROR = "io_error"
_INTERNAL_ERROR = "internal_error"


def diagnostic_code(error: BaseException) -> str:
    """Return the stable code for ``error``.

    Package errors resolve through the mapping above. Anything else falls back
    to a coarse builtin classification so an envelope always carries a code.
    """
    for klass in type(error).__mro__:
        code = DIAGNOSTIC_CODES.get(klass)
        if code is not None:
            return code
    if isinstance(error, OSError):
        return _IO_ERROR
    if isinstance(error, (ValueError, TypeError, LookupError)):
        return _INVALID_INPUT
    return _INTERNAL_ERROR


__all__ = ["DIAGNOSTIC_CODES", "UsageError", "diagnostic_code"]
