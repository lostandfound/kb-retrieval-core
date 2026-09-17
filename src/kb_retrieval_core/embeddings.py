"""Provider-independent embedding contracts and compatibility fingerprints.

This module defines values and validation only.  It imports no embedding SDK,
loads no model, and performs no network or filesystem access.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from math import isfinite
from types import MappingProxyType
from typing import Mapping, Protocol, Sequence, runtime_checkable

from ._normalization import normalize_json


def _required_string(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    return normalized


def _optional_string(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _required_string(value, name)


def _positive_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _freeze_json(value: object) -> object:
    normalized = normalize_json(value)
    if isinstance(normalized, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in normalized.items()})
    if isinstance(normalized, list):
        return tuple(_freeze_json(item) for item in normalized)
    return normalized


@dataclass(frozen=True, slots=True)
class EmbeddingConfig:
    """Compatibility-relevant identity of an embedding implementation."""

    implementation: str
    model: str
    dimension: int
    revision: str | None = None
    document_task: str | None = None
    query_task: str | None = None
    tokenizer: str | None = None
    preprocessing: Mapping[str, object] = field(default_factory=dict)
    pooling: str | None = None
    normalization: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "implementation", _required_string(self.implementation, "implementation"))
        object.__setattr__(self, "model", _required_string(self.model, "model"))
        object.__setattr__(self, "dimension", _positive_integer(self.dimension, "dimension"))
        for name in ("revision", "document_task", "query_task", "tokenizer", "pooling", "normalization"):
            object.__setattr__(self, name, _optional_string(getattr(self, name), name))
        if not isinstance(self.preprocessing, Mapping):
            raise TypeError("preprocessing must be a mapping")
        frozen = _freeze_json(self.preprocessing)
        if not isinstance(frozen, Mapping):  # pragma: no cover - guarded above
            raise TypeError("preprocessing must be a mapping")
        object.__setattr__(self, "preprocessing", frozen)

    @property
    def fingerprint(self) -> str:
        return embedding_fingerprint(self)

    def to_dict(self) -> dict[str, object]:
        return embedding_config_to_dict(self)


@dataclass(frozen=True, slots=True)
class VectorIndexConfig:
    """Compatibility identity added by vector storage and search semantics."""

    embedding: EmbeddingConfig
    similarity_metric: str
    format_version: int

    def __post_init__(self) -> None:
        if not isinstance(self.embedding, EmbeddingConfig):
            raise TypeError("embedding must be an EmbeddingConfig")
        object.__setattr__(
            self,
            "similarity_metric",
            _required_string(self.similarity_metric, "similarity_metric").casefold(),
        )
        object.__setattr__(
            self,
            "format_version",
            _positive_integer(self.format_version, "format_version"),
        )

    @property
    def fingerprint(self) -> str:
        return vector_index_fingerprint(self)

    def to_dict(self) -> dict[str, object]:
        return vector_index_config_to_dict(self)


@dataclass(frozen=True, slots=True)
class EmbeddingDocument:
    """An ordered document embedding request with a stable caller-owned ID."""

    document_id: str
    text: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "document_id", _required_string(self.document_id, "document_id"))
        if not isinstance(self.text, str):
            raise TypeError("text must be a string")
        if not self.text.strip():
            raise ValueError("text must not be empty")


@dataclass(frozen=True, slots=True)
class EmbeddedDocument:
    """A document vector paired with the input ID used to verify ordering."""

    document_id: str
    vector: tuple[float, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "document_id", _required_string(self.document_id, "document_id"))
        object.__setattr__(self, "vector", _coerce_vector(self.vector, "vector"))


@runtime_checkable
class Embedder(Protocol):
    """Minimal provider-independent embedding boundary."""

    @property
    def config(self) -> EmbeddingConfig: ...

    def embed_documents(self, documents: Sequence[EmbeddingDocument]) -> Sequence[EmbeddedDocument]: ...

    def embed_query(self, text: str) -> Sequence[float]: ...


def embedding_config_to_dict(config: EmbeddingConfig) -> dict[str, object]:
    if not isinstance(config, EmbeddingConfig):
        raise TypeError("config must be an EmbeddingConfig")
    return {
        "dimension": config.dimension,
        "document_task": config.document_task,
        "implementation": config.implementation,
        "model": config.model,
        "normalization": config.normalization,
        "pooling": config.pooling,
        "preprocessing": normalize_json(config.preprocessing),
        "query_task": config.query_task,
        "revision": config.revision,
        "tokenizer": config.tokenizer,
    }


def vector_index_config_to_dict(config: VectorIndexConfig) -> dict[str, object]:
    if not isinstance(config, VectorIndexConfig):
        raise TypeError("config must be a VectorIndexConfig")
    return {
        "embedding_fingerprint": config.embedding.fingerprint,
        "format_version": config.format_version,
        "similarity_metric": config.similarity_metric,
    }


def embedding_fingerprint(config: EmbeddingConfig) -> str:
    return _fingerprint(embedding_config_to_dict(config))


def vector_index_fingerprint(config: VectorIndexConfig) -> str:
    return _fingerprint(vector_index_config_to_dict(config))


def validate_document_embeddings(
    documents: Sequence[EmbeddingDocument],
    results: Sequence[EmbeddedDocument],
    *,
    dimension: int,
) -> tuple[EmbeddedDocument, ...]:
    """Validate exact cardinality, identity, order, and vector dimensions."""

    expected_dimension = _positive_integer(dimension, "dimension")
    requested = tuple(documents)
    returned = tuple(results)
    if any(not isinstance(item, EmbeddingDocument) for item in requested):
        raise TypeError("documents must contain only EmbeddingDocument values")
    if any(not isinstance(item, EmbeddedDocument) for item in returned):
        raise TypeError("results must contain only EmbeddedDocument values")
    requested_ids = tuple(item.document_id for item in requested)
    if len(requested_ids) != len(set(requested_ids)):
        raise ValueError("document IDs must be unique")
    returned_ids = tuple(item.document_id for item in returned)
    if len(returned_ids) != len(set(returned_ids)):
        raise ValueError("result document IDs must be unique")
    if len(returned) != len(requested):
        raise ValueError(
            f"embedding result count {len(returned)} does not match document count {len(requested)}"
        )
    if returned_ids != requested_ids:
        raise ValueError("embedding results must preserve requested document ID order")
    for item in returned:
        if len(item.vector) != expected_dimension:
            raise ValueError(
                f"embedding for {item.document_id!r} has dimension {len(item.vector)}; "
                f"expected {expected_dimension}"
            )
    return returned


def validate_query_embedding(vector: Sequence[float], *, dimension: int) -> tuple[float, ...]:
    """Validate one query vector against its configured dimension."""

    expected_dimension = _positive_integer(dimension, "dimension")
    result = _coerce_vector(vector, "query vector")
    if len(result) != expected_dimension:
        raise ValueError(f"query vector has dimension {len(result)}; expected {expected_dimension}")
    return result


def _coerce_vector(value: object, name: str) -> tuple[float, ...]:
    if isinstance(value, (str, bytes)):
        raise TypeError(f"{name} must be a sequence of numbers")
    try:
        values = tuple(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise TypeError(f"{name} must be a sequence of numbers") from exc
    converted: list[float] = []
    for component in values:
        if isinstance(component, bool) or not isinstance(component, (int, float)):
            raise TypeError(f"{name} must contain only numbers")
        number = float(component)
        if not isfinite(number):
            raise ValueError(f"{name} must contain only finite numbers")
        converted.append(number)
    return tuple(converted)


def _fingerprint(value: Mapping[str, object]) -> str:
    canonical = json.dumps(
        normalize_json(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


__all__ = [
    "Embedder",
    "EmbeddingConfig",
    "EmbeddingDocument",
    "EmbeddedDocument",
    "VectorIndexConfig",
    "embedding_config_to_dict",
    "embedding_fingerprint",
    "validate_document_embeddings",
    "validate_query_embedding",
    "vector_index_config_to_dict",
    "vector_index_fingerprint",
]
