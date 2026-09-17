"""Immutable, domain-independent retrieval value objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from math import isfinite
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


def _path(value: str, field_name: str = "entity_path") -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value.startswith("/"):
        raise ValueError(f"{field_name} must be bundle-root-relative")
    return value


def _string(value: str, field_name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not allow_empty and not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    return value


def _strings(values: object, field_name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{field_name} must be a collection of strings")
    try:
        result = tuple(values)  # type: ignore[arg-type]
    except TypeError as exc:
        raise TypeError(f"{field_name} must be a collection of strings") from exc
    if any(not isinstance(item, str) for item in result):
        raise TypeError(f"{field_name} must contain only strings")
    return result


def _freeze(value: object, field_name: str) -> object:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError(f"{field_name} keys must be strings")
        return MappingProxyType({key: _freeze(item, field_name) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item, field_name) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item, field_name) for item in value)
    return value


def _mapping(value: Mapping[str, object], field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    return MappingProxyType({key: _freeze(item, field_name) for key, item in value.items()})


def _confidence(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise TypeError("confidence must be a non-empty string or None")
    return value


@dataclass(frozen=True, slots=True)
class Relation:
    """An established graph relation and its supporting source IDs."""

    predicate: str
    target: str
    source_ids: tuple[str, ...] = ()
    source_path: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)
    confidence: str | None = None
    owner_source_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _string(self.predicate, "predicate")
        _string(self.target, "target")
        if self.source_path is not None:
            _path(self.source_path, "source_path")
        object.__setattr__(self, "source_ids", _strings(self.source_ids, "source_ids"))
        object.__setattr__(self, "owner_source_ids", _strings(self.owner_source_ids, "owner_source_ids"))
        object.__setattr__(self, "confidence", _confidence(self.confidence))
        object.__setattr__(self, "metadata", _mapping(self.metadata, "metadata"))

    @property
    def relation_source_ids(self) -> tuple[str, ...]:
        return self.source_ids or self.owner_source_ids


@dataclass(frozen=True, slots=True)
class Claim:
    """A source-addressable assertion whose status is ontology-owned."""

    claim_id: str | None = None
    statement: str | None = None
    status: str | None = None
    confidence: str | None = None
    source_ids: tuple[str, ...] = ()
    subject: str | None = None
    predicate: str | None = None
    target: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)
    claim_path: str | None = None
    property: str | None = None
    value: object | None = None

    def __post_init__(self) -> None:
        for name in ("claim_id", "statement", "status", "subject", "predicate", "target", "property"):
            value = getattr(self, name)
            if value is not None:
                _string(value, name)
        if self.claim_path is not None:
            _path(self.claim_path, "claim_path")
        if self.status is not None and not self.status.strip():
            raise ValueError("status must be non-empty when provided")
        relation_fields = self.predicate is not None or self.target is not None
        value_fields = self.property is not None or self.value is not None
        if relation_fields and value_fields:
            raise ValueError("claim relation and value forms are mutually exclusive")
        relation_complete = all(
            isinstance(item, str) and bool(item.strip())
            for item in (self.subject, self.predicate, self.target)
        )
        value_complete = (
            isinstance(self.subject, str)
            and bool(self.subject.strip())
            and isinstance(self.property, str)
            and bool(self.property.strip())
            and self.value is not None
        )
        if (relation_fields or value_fields) and not (relation_complete or value_complete):
            raise ValueError("claim requires a complete relation or value form")
        object.__setattr__(self, "confidence", _confidence(self.confidence))
        object.__setattr__(self, "source_ids", _strings(self.source_ids, "source_ids"))
        object.__setattr__(self, "metadata", _mapping(self.metadata, "metadata"))
        if self.value is not None:
            object.__setattr__(self, "value", _freeze(self.value, "value"))


@dataclass(frozen=True, slots=True)
class Reference:
    """Bibliographic/source metadata keyed by a normalized bare ID."""

    reference_id: str
    title: str = ""
    authors: tuple[str, ...] = ()
    year: int | None = None
    url: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)
    author: str | None = None

    def __post_init__(self) -> None:
        _string(self.reference_id, "reference_id")
        if self.title:
            _string(self.title, "title")
        if self.author is not None:
            _string(self.author, "author")
        normalized_authors = _strings(self.authors, "authors")
        if self.author is not None:
            if normalized_authors and normalized_authors != (self.author,):
                raise ValueError("author and authors disagree")
            normalized_authors = (self.author,)
        if self.url is not None:
            _string(self.url, "url")
        if self.year is not None and (isinstance(self.year, bool) or not isinstance(self.year, int)):
            raise TypeError("year must be an integer or None")
        object.__setattr__(self, "authors", normalized_authors)
        object.__setattr__(self, "metadata", _mapping(self.metadata, "metadata"))

    @property
    def id(self) -> str:
        return self.reference_id

    @property
    def source_id(self) -> str:
        return self.reference_id


@dataclass(frozen=True, slots=True)
class Entity:
    """A retrievable Markdown document and its owner provenance."""

    entity_path: str
    entity_type: str
    title: str
    tags: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()
    relations: tuple[Relation, ...] = ()
    claims: tuple[Claim, ...] = ()
    aliases: tuple[str, ...] = ()
    description: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)
    content: str = ""
    timestamp: str | None = None

    def __post_init__(self) -> None:
        _path(self.entity_path)
        _string(self.entity_type, "entity_type")
        _string(self.title, "title")
        if self.description is not None:
            _string(self.description, "description")
        _string(self.content, "content", allow_empty=True)
        if self.timestamp is not None:
            _string(self.timestamp, "timestamp")
        object.__setattr__(self, "tags", _strings(self.tags, "tags"))
        object.__setattr__(self, "source_ids", _strings(self.source_ids, "source_ids"))
        object.__setattr__(self, "relations", tuple(self.relations))
        object.__setattr__(self, "claims", tuple(self.claims))
        object.__setattr__(self, "aliases", _strings(self.aliases, "aliases"))
        object.__setattr__(self, "metadata", _mapping(self.metadata, "metadata"))
        if any(not isinstance(item, Relation) for item in self.relations):
            raise TypeError("relations must contain only Relation objects")
        if any(not isinstance(item, Claim) for item in self.claims):
            raise TypeError("claims must contain only Claim objects")


@dataclass(frozen=True, slots=True)
class Chunk:
    """A deterministic heading-owned passage derived from an Entity."""

    chunk_id: str
    entity_path: str
    entity_type: str
    title: str
    heading: str | None
    text: str
    content_hash: str
    tags: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()
    relations: tuple[Relation, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)
    # Zero-based source order within the owning entity.  It is appended with a
    # default to keep older positional construction source-compatible.
    ordinal: int = 0

    def __post_init__(self) -> None:
        _string(self.chunk_id, "chunk_id")
        _path(self.entity_path)
        _string(self.entity_type, "entity_type")
        _string(self.title, "title")
        if self.heading is not None:
            _string(self.heading, "heading")
        _string(self.text, "text")
        _string(self.content_hash, "content_hash")
        if isinstance(self.ordinal, bool) or not isinstance(self.ordinal, int) or self.ordinal < 0:
            raise ValueError("ordinal must be a non-negative integer")
        object.__setattr__(self, "tags", _strings(self.tags, "tags"))
        object.__setattr__(self, "source_ids", _strings(self.source_ids, "source_ids"))
        object.__setattr__(self, "relations", tuple(self.relations))
        object.__setattr__(self, "metadata", _mapping(self.metadata, "metadata"))
        if any(not isinstance(item, Relation) for item in self.relations):
            raise TypeError("relations must contain only Relation objects")


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Immutable loaded source snapshot."""

    entities: tuple[Entity, ...] = ()
    chunks: tuple[Chunk, ...] = ()
    relations: tuple[Relation, ...] = ()
    claims: tuple[Claim, ...] = ()
    references: tuple[Reference, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        entities = tuple(self.entities)
        chunks = tuple(self.chunks)
        relations = tuple(self.relations)
        claims = tuple(self.claims)
        references = tuple(self.references)
        for name, values, typ in (
            ("entities", entities, Entity),
            ("chunks", chunks, Chunk),
            ("relations", relations, Relation),
            ("claims", claims, Claim),
            ("references", references, Reference),
        ):
            if any(not isinstance(item, typ) for item in values):
                raise TypeError(f"{name} must contain only {typ.__name__} objects")
        _unique((entity.entity_path for entity in entities), "entity paths")
        _unique((chunk.chunk_id for chunk in chunks), "chunk IDs")
        _unique((reference.reference_id for reference in references), "reference IDs")
        entity_paths = {entity.entity_path for entity in entities}
        for chunk in chunks:
            if chunk.entity_path not in entity_paths:
                raise ValueError(f"chunk {chunk.chunk_id!r} refers to unknown entity path")
        all_relations = relations + tuple(relation for entity in entities for relation in entity.relations) + tuple(
            relation for chunk in chunks for relation in chunk.relations
        )
        for relation in all_relations:
            if relation.source_path is not None and relation.source_path not in entity_paths:
                raise ValueError(f"relation source path {relation.source_path!r} is unknown")
            if relation.target.startswith("/") and relation.target not in entity_paths:
                raise ValueError(f"relation target path {relation.target!r} is unknown")
        all_claims = claims + tuple(claim for entity in entities for claim in entity.claims)
        for claim in all_claims:
            for field_name in ("subject", "target"):
                value = getattr(claim, field_name)
                if value is not None and value.startswith("/") and value not in entity_paths:
                    raise ValueError(f"claim {field_name} path {value!r} is unknown")
        object.__setattr__(self, "entities", entities)
        object.__setattr__(self, "chunks", chunks)
        object.__setattr__(self, "relations", relations)
        object.__setattr__(self, "claims", claims)
        object.__setattr__(self, "references", references)
        object.__setattr__(self, "metadata", _mapping(self.metadata, "metadata"))


def _unique(values: object, description: str) -> None:
    values = tuple(values)  # type: ignore[arg-type]
    if len(values) != len(set(values)):
        raise ValueError(f"{description} must be unique")


@dataclass(frozen=True, slots=True)
class Evidence:
    """A source-addressable passage returned by a retrieval backend."""

    entity_path: str
    text: str
    section: str | None = None
    source_ids: tuple[str, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)
    passage_source_ids: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        _path(self.entity_path)
        _string(self.text, "text")
        if self.section is not None:
            _string(self.section, "section")
        normalized = _strings(self.source_ids, "source_ids")
        passage = normalized if self.passage_source_ids is None else _strings(self.passage_source_ids, "passage_source_ids")
        object.__setattr__(self, "source_ids", normalized)
        object.__setattr__(self, "passage_source_ids", passage)
        object.__setattr__(self, "metadata", _mapping(self.metadata, "metadata"))


@dataclass(frozen=True, slots=True)
class SearchHit:
    """A ranked backend-independent evidence item."""

    evidence: Evidence
    score: float
    rank: int
    retriever: str

    def __post_init__(self) -> None:
        if not isinstance(self.evidence, Evidence):
            raise TypeError("evidence must be an Evidence object")
        if isinstance(self.rank, bool) or not isinstance(self.rank, int) or self.rank < 1:
            raise ValueError("rank must be at least 1")
        if isinstance(self.score, bool) or not isinstance(self.score, (int, float)):
            raise TypeError("score must be a number")
        if not isfinite(float(self.score)):
            raise ValueError("score must be finite")
        _string(self.retriever, "retriever")


__all__ = [
    "Claim", "Chunk", "Entity", "Evidence", "Reference", "Relation", "SearchHit", "Snapshot",
]
