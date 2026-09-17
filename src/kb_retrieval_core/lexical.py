"""Dependency-free deterministic lexical retrieval.

Text is normalized with Unicode NFKC, case-folding, and whitespace removal.
The default character n-gram size is 2; one-character query/document strings
use unigrams so short queries still work. Matching uses query n-gram coverage,
with an exact-field and substring bonus. Entity fields intentionally have
unequal weights: title 8, aliases 7, description 2, tags 2, type 1, and body
1. Chunk fields are searched separately with text 4, heading 5, title 3,
tags 1, and type 1. These are deterministic baseline choices, not ontology
or ranking policy.

LexicalIndex derives chunks with chunk_snapshot when the supplied Snapshot has
no chunks. Entity and chunk searches return backend-independent SearchHit
objects whose Evidence retains path, section, source IDs, and identifiers in
metadata. Results are sorted by descending score and then stable path/ID
keys. Empty or normalization-empty queries and non-positive top_k values raise
ValueError.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Iterable

from ._normalization import normalize_source_id
from .chunking import chunk_snapshot
from .models import Chunk, Entity, Evidence, SearchHit, Snapshot

DEFAULT_NGRAM_SIZE = 2

_ENTITY_WEIGHTS = {
    "title": 8.0,
    "aliases": 7.0,
    "description": 2.0,
    "tags": 2.0,
    "entity_type": 1.0,
    "content": 1.0,
}
_CHUNK_WEIGHTS = {
    "text": 4.0,
    "heading": 5.0,
    "title": 3.0,
    "tags": 1.0,
    "entity_type": 1.0,
}


def normalize_text(value: str) -> str:
    """Return the deterministic comparison form used by the index."""

    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if not character.isspace())


def character_ngrams(value: str, ngram_size: int = DEFAULT_NGRAM_SIZE) -> frozenset[str]:
    """Return unique overlapping character n-grams for normalized text."""

    if not isinstance(ngram_size, int) or isinstance(ngram_size, bool) or ngram_size < 1:
        raise ValueError("ngram_size must be a positive integer")
    normalized = normalize_text(value)
    if not normalized:
        return frozenset()
    if len(normalized) <= ngram_size:
        return frozenset({normalized})
    return frozenset(
        normalized[index : index + ngram_size]
        for index in range(len(normalized) - ngram_size + 1)
    )


@dataclass(frozen=True, slots=True)
class _Prepared:
    value: str
    grams: frozenset[str]


class LexicalIndex:
    """An in-memory lexical index over immutable snapshot values."""

    def __init__(self, snapshot: Snapshot, *, ngram_size: int = DEFAULT_NGRAM_SIZE) -> None:
        if not isinstance(snapshot, Snapshot):
            raise TypeError("snapshot must be a Snapshot")
        if not isinstance(ngram_size, int) or isinstance(ngram_size, bool) or ngram_size < 1:
            raise ValueError("ngram_size must be a positive integer")
        self.ngram_size = ngram_size
        self.snapshot = chunk_snapshot(snapshot) if not snapshot.chunks else snapshot
        self._entities = tuple(self.snapshot.entities)
        self._chunks = tuple(self.snapshot.chunks)
        self._entity_fields = tuple(
            (
                entity,
                {
                    "title": _prepare(entity.title, ngram_size),
                    "aliases": tuple(_prepare(value, ngram_size) for value in entity.aliases),
                    "description": _prepare(entity.description or "", ngram_size),
                    "tags": tuple(_prepare(value, ngram_size) for value in entity.tags),
                    "entity_type": _prepare(entity.entity_type, ngram_size),
                    "content": _prepare(entity.content, ngram_size),
                },
            )
            for entity in self._entities
        )
        self._chunk_fields = tuple(
            (
                chunk,
                {
                    "text": _prepare(chunk.text, ngram_size),
                    "heading": _prepare(chunk.heading or "", ngram_size),
                    "title": _prepare(chunk.title, ngram_size),
                    "tags": tuple(_prepare(value, ngram_size) for value in chunk.tags),
                    "entity_type": _prepare(chunk.entity_type, ngram_size),
                },
            )
            for chunk in self._chunks
        )

    @classmethod
    def from_snapshot(
        cls, snapshot: Snapshot, *, ngram_size: int = DEFAULT_NGRAM_SIZE
    ) -> LexicalIndex:
        return cls(snapshot, ngram_size=ngram_size)

    def search_entities(self, query: str, top_k: int = 5) -> tuple[SearchHit, ...]:
        query_prepared = self._query(query, top_k)
        candidates: list[tuple[float, str, SearchHit]] = []
        for entity, fields in self._entity_fields:
            score = _score_fields(query_prepared, fields, _ENTITY_WEIGHTS)
            if score <= 0:
                continue
            evidence = Evidence(
                entity_path=entity.entity_path,
                text=(entity.content.strip() or entity.description or entity.title),
                    source_ids=tuple(normalize_source_id(value, allow_empty=True) for value in entity.source_ids),
                metadata={
                    "result_type": "entity",
                    "entity_path": entity.entity_path,
                    "entity_type": entity.entity_type,
                    "title": entity.title,
                    "aliases": entity.aliases,
                    "claims": tuple(
                        {
                            "claim_id": claim.claim_id,
                            "claim_path": claim.claim_path,
                            "status": claim.status,
                            "confidence": claim.confidence,
                            "source_ids": claim.source_ids,
                            "subject": claim.subject,
                            "predicate": claim.predicate,
                            "target": claim.target,
                            "property": claim.property,
                            "value": claim.value,
                        }
                        for claim in entity.claims
                    ),
                },
            )
            candidates.append(
                (
                    score,
                    entity.entity_path,
                    SearchHit(evidence=evidence, score=score, rank=1, retriever="lexical.entity"),
                )
            )
        return _rank(candidates, top_k)

    def search_chunks(self, query: str, top_k: int = 5) -> tuple[SearchHit, ...]:
        query_prepared = self._query(query, top_k)
        candidates: list[tuple[float, str, SearchHit]] = []
        for chunk, fields in self._chunk_fields:
            score = _score_fields(query_prepared, fields, _CHUNK_WEIGHTS)
            if score <= 0:
                continue
            evidence = Evidence(
                entity_path=chunk.entity_path,
                section=chunk.heading,
                text=chunk.text,
                    source_ids=tuple(normalize_source_id(value, allow_empty=True) for value in chunk.source_ids),
                metadata={
                    "result_type": "chunk",
                    "chunk_id": chunk.chunk_id,
                    "entity_path": chunk.entity_path,
                    "entity_type": chunk.entity_type,
                    "title": chunk.title,
                    "content_hash": chunk.content_hash,
                },
            )
            candidates.append(
                (
                    score,
                    chunk.chunk_id,
                    SearchHit(evidence=evidence, score=score, rank=1, retriever="lexical.chunk"),
                )
            )
        return _rank(candidates, top_k)

    def search(self, query: str, top_k: int = 5) -> tuple[SearchHit, ...]:
        """Search chunks, the passage-oriented default retrieval surface."""

        return self.search_chunks(query, top_k=top_k)

    def _query(self, query: str, top_k: int) -> _Prepared:
        _validate_top_k(top_k)
        if not isinstance(query, str):
            raise TypeError("query must be a string")
        prepared = _prepare(query, self.ngram_size)
        if not prepared.value:
            raise ValueError("query must not be empty after normalization")
        return prepared


def build_lexical_index(
    snapshot: Snapshot, *, ngram_size: int = DEFAULT_NGRAM_SIZE
) -> LexicalIndex:
    """Build a deterministic in-memory index from a Snapshot."""

    return LexicalIndex(snapshot, ngram_size=ngram_size)


def _prepare(value: str, ngram_size: int) -> _Prepared:
    normalized = normalize_text(value)
    return _Prepared(
        normalized,
        character_ngrams(normalized, ngram_size),
    )


def _score_fields(
    query: _Prepared,
    fields: dict[str, _Prepared | tuple[_Prepared, ...]],
    weights: dict[str, float],
) -> float:
    score = 0.0
    for name, weight in weights.items():
        value = fields[name]
        values = value if isinstance(value, tuple) else (value,)
        best = max((_field_score(query, item) for item in values), default=0.0)
        score += weight * best
    return score


def _field_score(query: _Prepared, value: _Prepared) -> float:
    if not value.value or not value.grams:
        return 0.0
    # Direct containment also covers one-character (or otherwise shorter-than-
    # ngram) queries, which have no overlapping bigram to count.
    coverage = (
        1.0 if query.value in value.value else len(query.grams & value.grams) / len(query.grams)
    )
    if coverage <= 0:
        return 0.0
    score = coverage
    if query.value == value.value:
        score += 2.0
    elif query.value in value.value:
        score += 0.5
    return score


def _rank(
    candidates: Iterable[tuple[float, str, SearchHit]], top_k: int
) -> tuple[SearchHit, ...]:
    ordered = sorted(candidates, key=lambda item: (-item[0], item[1]))
    return tuple(
        SearchHit(
            evidence=hit.evidence,
            score=hit.score,
            rank=rank,
            retriever=hit.retriever,
        )
        for rank, (_score, _key, hit) in enumerate(ordered[:top_k], start=1)
    )


def _validate_top_k(top_k: int) -> None:
    if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 1:
        raise ValueError("top_k must be a positive integer")


__all__ = [
    "DEFAULT_NGRAM_SIZE",
    "LexicalIndex",
    "build_lexical_index",
    "character_ngrams",
    "normalize_text",
]
