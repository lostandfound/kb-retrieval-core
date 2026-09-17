"""Deterministic vector-only retrieval over persisted vector sidecar."""

from __future__ import annotations

from math import sqrt
from typing import Sequence

from ._normalization import normalize_source_id
from .chunking import chunk_snapshot
from .embeddings import VectorIndexConfig, validate_query_embedding
from .graph_search import CONFIDENCE_WEIGHTS, DEFAULT_CLAIM_CONFIDENCE
from .models import Claim, Chunk, Evidence, Relation, SearchHit, Snapshot
from .vector_store import SQLiteVectorSidecar


class VectorRetrievalError(ValueError):
    """Raised when vector retrieval inputs or compatibility checks fail."""


class VectorRetriever:
    """Search persisted chunk vectors while preserving Snapshot provenance."""

    def __init__(self, snapshot: Snapshot, sidecar: SQLiteVectorSidecar, embedder: object, *, snapshot_hash: str, chunk_hash: str) -> None:
        if not isinstance(snapshot, Snapshot):
            raise TypeError("snapshot must be a Snapshot")
        if not isinstance(sidecar, SQLiteVectorSidecar):
            raise TypeError("sidecar must be a SQLiteVectorSidecar")
        if not hasattr(embedder, "config") or not hasattr(embedder, "embed_query"):
            raise TypeError("embedder must expose config and embed_query")
        prepared = chunk_snapshot(snapshot) if not snapshot.chunks else snapshot
        config = VectorIndexConfig(embedder.config, str(sidecar.manifest["similarity_metric"]), int(sidecar.manifest["vector_format_version"]))
        if not sidecar.matches(config=config, snapshot_hash=snapshot_hash, chunk_hash=chunk_hash):
            raise VectorRetrievalError("vector sidecar is incompatible with snapshot, embedder, or vector configuration")
        chunks = {chunk.chunk_id: chunk for chunk in prepared.chunks}
        records = {record.chunk_id: record for record in sidecar.records}
        if set(chunks) != set(records):
            missing = sorted(set(chunks) - set(records))
            extra = sorted(set(records) - set(chunks))
            raise VectorRetrievalError(f"vector sidecar chunk IDs do not match Snapshot (missing={missing}, extra={extra})")
        for chunk_id, record in records.items():
            if chunks[chunk_id].content_hash != record.content_hash:
                raise VectorRetrievalError(f"vector sidecar content hash mismatch for chunk {chunk_id!r}")
        self.snapshot, self.sidecar, self.embedder = snapshot, sidecar, embedder
        self._chunks, self._records, self._metric = chunks, records, config.similarity_metric

    def search(self, query: str, *, top_k: int = 5) -> tuple[SearchHit, ...]:
        if isinstance(top_k, bool):
            raise ValueError("top_k must be an integer")
        if not isinstance(top_k, int):
            raise TypeError("top_k must be an integer")
        if top_k < 1:
            raise ValueError("top_k must be at least 1")
        if not isinstance(query, str):
            raise TypeError("query must be a string")
        if not query.strip():
            raise ValueError("query must not be empty")
        try:
            query_vector = validate_query_embedding(self.embedder.embed_query(query), dimension=int(self.sidecar.manifest["dimension"]))
        except (TypeError, ValueError) as exc:
            raise VectorRetrievalError(f"query embedding is invalid: {exc}") from exc
        scored = [(self._score(query_vector, record.vector), record.chunk_id) for record in self._records.values()]
        ordered = sorted(scored, key=lambda item: (-item[0], item[1]))[:top_k]
        return tuple(SearchHit(evidence=self._evidence(chunk_id), score=score, rank=rank, retriever="vector.chunk") for rank, (score, chunk_id) in enumerate(ordered, start=1))

    def _score(self, query: Sequence[float], vector: Sequence[float]) -> float:
        if self._metric == "dot":
            return float(sum(left * right for left, right in zip(query, vector)))
        if self._metric == "cosine":
            query_norm, vector_norm = sqrt(sum(item * item for item in query)), sqrt(sum(item * item for item in vector))
            if query_norm == 0.0 or vector_norm == 0.0:
                return 0.0
            return float(sum(left * right for left, right in zip(query, vector)) / (query_norm * vector_norm))
        raise VectorRetrievalError(f"unsupported similarity metric {self._metric!r}; expected 'cosine' or 'dot'")

    def _evidence(self, chunk_id: str) -> Evidence:
        chunk = self._chunks[chunk_id]
        entity = next(entity for entity in self.snapshot.entities if entity.entity_path == chunk.entity_path)
        relations = _applicable_relations(self.snapshot, entity.entity_path, chunk)
        claims = _applicable_claims(self.snapshot, entity.entity_path, entity)
        metadata: dict[str, object] = {"result_type": "vector.chunk", "chunk_id": chunk.chunk_id, "content_hash": chunk.content_hash, "vector_index_fingerprint": self.sidecar.manifest["vector_index_fingerprint"]}
        assertions = [_relation_metadata(relation, entity.entity_path, entity.source_ids) for relation in relations]
        assertions.extend(_claim_metadata(claim) for claim in claims)
        if assertions:
            metadata["assertions"] = tuple(assertions)
            for assertion in assertions:
                metadata.update(assertion)
        relation_ids = tuple(dict.fromkeys(source_id for relation in relations for source_id in _relation_source_ids(relation, entity.source_ids)))
        claim_ids = tuple(dict.fromkeys(normalize_source_id(source_id, allow_empty=True) for claim in claims for source_id in claim.source_ids))
        assertion_ids = tuple(dict.fromkeys(relation_ids + claim_ids))
        if assertion_ids:
            metadata["relation_source_ids"], metadata["claim_source_ids"] = assertion_ids, claim_ids
        metadata["requires_hedging"] = any(value in {"C", "D"} for value in (metadata.get("confidence"),)) or any(claim.confidence in {"C", "D"} for claim in claims) or any(relation.confidence in {"C", "D"} for relation in relations)
        passage_ids = tuple(normalize_source_id(value, allow_empty=True) for value in chunk.source_ids)
        return Evidence(entity_path=chunk.entity_path, section=chunk.heading, text=chunk.text, source_ids=passage_ids, passage_source_ids=passage_ids, metadata=metadata)


def _applicable_relations(snapshot: Snapshot, entity_path: str, chunk: Chunk) -> tuple[Relation, ...]:
    entity = next(entity for entity in snapshot.entities if entity.entity_path == entity_path)
    candidates = list(chunk.relations) + list(entity.relations) + [relation for relation in snapshot.relations if relation.source_path == entity_path]
    return _dedupe(candidates, lambda relation: (relation.source_path, relation.predicate, relation.target, relation.confidence, relation.source_ids))


def _applicable_claims(snapshot: Snapshot, entity_path: str, entity: object) -> tuple[Claim, ...]:
    candidates = list(getattr(entity, "claims", ())) + [claim for claim in snapshot.claims if claim.subject == entity_path]
    return _dedupe(candidates, lambda claim: (claim.claim_path, claim.claim_id, claim.subject, claim.predicate, claim.property, claim.target, claim.value))


def _dedupe(values: Sequence[object], key) -> tuple:
    result, seen = [], set()
    for value in values:
        marker = key(value)
        if marker not in seen:
            seen.add(marker)
            result.append(value)
    return tuple(result)


def _relation_source_ids(relation: Relation, owner_ids: Sequence[str]) -> tuple[str, ...]:
    values = relation.source_ids or relation.owner_source_ids or tuple(owner_ids)
    return tuple(dict.fromkeys(normalize_source_id(value, allow_empty=True) for value in values))


def _relation_metadata(relation: Relation, owner_path: str, owner_ids: Sequence[str]) -> dict[str, object]:
    weight = CONFIDENCE_WEIGHTS.get(relation.confidence, DEFAULT_CLAIM_CONFIDENCE) if relation.confidence else 1.0
    return {"relation": {"predicate": relation.predicate, "direction": "outgoing", "source_path": relation.source_path or owner_path, "target_path": relation.target}, "relation_source_ids": _relation_source_ids(relation, owner_ids), "claim_status": None, "confidence": relation.confidence, "confidence_weight": weight, "claim_id": None, "claim_path": None, "claim_source_ids": (), "affirmative": True}


def _claim_metadata(claim: Claim) -> dict[str, object]:
    ids = tuple(normalize_source_id(value, allow_empty=True) for value in claim.source_ids)
    status = claim.status.casefold() if isinstance(claim.status, str) else None
    metadata: dict[str, object] = {"relation_source_ids": ids, "claim_source_ids": ids, "claim_status": status, "confidence": claim.confidence, "confidence_weight": CONFIDENCE_WEIGHTS.get(claim.confidence, DEFAULT_CLAIM_CONFIDENCE), "claim_id": claim.claim_id, "claim_path": claim.claim_path, "affirmative": status == "accepted" if status is not None else None, "property": claim.property, "value": claim.value}
    if claim.predicate is not None:
        metadata["relation"] = {"predicate": claim.predicate, "direction": "outgoing", "source_path": claim.subject, "target_path": claim.target}
    return metadata


def build_vector_retriever(snapshot: Snapshot, sidecar: SQLiteVectorSidecar, embedder: object, *, snapshot_hash: str, chunk_hash: str) -> VectorRetriever:
    return VectorRetriever(snapshot, sidecar, embedder, snapshot_hash=snapshot_hash, chunk_hash=chunk_hash)


__all__ = ["VectorRetrievalError", "VectorRetriever", "build_vector_retriever"]
