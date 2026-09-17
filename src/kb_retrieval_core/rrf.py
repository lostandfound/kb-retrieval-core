"""Deterministic entity-level Reciprocal Rank Fusion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from ._normalization import normalize_source_id
from .models import Entity, Evidence, SearchHit, Snapshot


class FusionError(ValueError):
    """Raised when ranked backend inputs cannot be fused."""


@dataclass(frozen=True, slots=True)
class RRFConfig:
    """Reproducible parameters recorded on every fused hit."""

    constant: float = 60.0
    backend_weights: Mapping[str, float] = None  # type: ignore[assignment]
    backend_cutoffs: Mapping[str, int] = None  # type: ignore[assignment]
    passage_precedence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.constant, bool) or not isinstance(self.constant, (int, float)) or self.constant <= 0:
            raise ValueError("RRF constant must be a positive number")
        weights = {} if self.backend_weights is None else dict(self.backend_weights)
        cutoffs = {} if self.backend_cutoffs is None else dict(self.backend_cutoffs)
        if any(not isinstance(name, str) or not name.strip() for name in (*weights, *cutoffs)):
            raise ValueError("backend names must be non-empty strings")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 for value in weights.values()):
            raise ValueError("backend weights must be non-negative numbers")
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in cutoffs.values()):
            raise ValueError("backend cutoffs must be positive integers")
        object.__setattr__(self, "constant", float(self.constant))
        object.__setattr__(self, "backend_weights", weights)
        object.__setattr__(self, "backend_cutoffs", cutoffs)
        object.__setattr__(self, "passage_precedence", tuple(self.passage_precedence))

    def as_dict(self) -> dict[str, object]:
        return {
            "constant": self.constant,
            "backend_weights": dict(sorted(self.backend_weights.items())),
            "backend_cutoffs": dict(sorted(self.backend_cutoffs.items())),
            "passage_precedence": list(self.passage_precedence),
        }


def fuse_entity_rankings(
    rankings: Mapping[str, Iterable[SearchHit]] | Iterable[tuple[str, Iterable[SearchHit]]],
    *,
    top_k: int | None = None,
    config: RRFConfig | None = None,
    snapshot: Snapshot | None = None,
) -> tuple[SearchHit, ...]:
    """Fuse ranked backend results into one deterministic hit per entity.

    Ranks are one-based within each backend. Duplicate chunks from one backend
    are collapsed to its best-ranked chunk before scoring. Backend scores are
    never used in the fusion calculation.
    """
    if top_k is not None and (isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1):
        raise ValueError("top_k must be a positive integer")
    if snapshot is not None and not isinstance(snapshot, Snapshot):
        raise TypeError("snapshot must be a Snapshot")
    selected_config = RRFConfig() if config is None else config
    if not isinstance(selected_config, RRFConfig):
        raise TypeError("config must be an RRFConfig")
    pairs = rankings.items() if isinstance(rankings, Mapping) else rankings
    backends: dict[str, tuple[SearchHit, ...]] = {}
    for name, values in pairs:
        if not isinstance(name, str) or not name.strip():
            raise FusionError("backend names must be non-empty strings")
        if name in backends:
            raise FusionError(f"duplicate backend {name!r}")
        hits = tuple(values)
        if any(not isinstance(hit, SearchHit) for hit in hits):
            raise TypeError("backend rankings must contain SearchHit objects")
        cutoff = selected_config.backend_cutoffs.get(name)
        if cutoff is not None:
            hits = hits[:cutoff]
        backends[name] = hits
    entities: dict[str, dict[str, object]] = {}
    for backend, hits in backends.items():
        weight = selected_config.backend_weights.get(backend, 1.0)
        per_entity: dict[str, SearchHit] = {}
        for position, hit in enumerate(hits, start=1):
            path = hit.evidence.entity_path
            previous = per_entity.get(path)
            if previous is None or (position, _candidate_key(hit)) < (previous.rank, _candidate_key(previous)):
                per_entity[path] = hit
        for path, hit in per_entity.items():
            item = entities.setdefault(path, {"candidates": {}, "score": 0.0})
            candidates = item["candidates"]
            assert isinstance(candidates, dict)
            candidates[backend] = hit
            item["score"] = float(item["score"]) + weight / (selected_config.constant + hit.rank)
    ordered = sorted(entities.items(), key=lambda item: (-float(item[1]["score"]), item[0]))
    if top_k is not None:
        ordered = ordered[:top_k]
    result: list[SearchHit] = []
    for rank, (path, item) in enumerate(ordered, start=1):
        candidates = item["candidates"]
        assert isinstance(candidates, dict)
        evidence = _select_evidence(path, candidates, selected_config, snapshot)
        metadata = dict(evidence.metadata)
        metadata["rrf"] = {
            "constant": selected_config.constant,
            "backend_weights": dict(sorted(selected_config.backend_weights.items())),
            "backend_cutoffs": dict(sorted(selected_config.backend_cutoffs.items())),
            "passage_precedence": list(selected_config.passage_precedence),
            "backend_ranks": {name: hit.rank for name, hit in sorted(candidates.items())},
            "entity_path": path,
        }
        fused = Evidence(
            entity_path=evidence.entity_path,
            section=evidence.section,
            text=evidence.text,
            source_ids=evidence.source_ids,
            passage_source_ids=evidence.passage_source_ids,
            metadata=metadata,
        )
        result.append(SearchHit(fused, float(item["score"]), rank, "rrf.entity"))
    return tuple(result)


def _candidate_key(hit: SearchHit) -> tuple[object, ...]:
    chunk_id = hit.evidence.metadata.get("chunk_id", "")
    return (str(chunk_id), hit.evidence.section or "", hit.evidence.text)


def _select_evidence(path: str, candidates: Mapping[str, SearchHit], config: RRFConfig, snapshot: Snapshot | None) -> Evidence:
    precedence = config.passage_precedence or tuple(candidates)
    ordered = sorted(candidates.items(), key=lambda pair: (precedence.index(pair[0]) if pair[0] in precedence else len(precedence), pair[1].rank, _candidate_key(pair[1]), pair[0]))
    passage = next((hit for _, hit in ordered if "chunk_id" in hit.evidence.metadata), None)
    chosen = passage or ordered[0][1]
    if passage is None and snapshot is not None:
        fallback = _snapshot_fallback(snapshot, path)
        if fallback is not None:
            return fallback
    evidences = [candidate.evidence for _, candidate in ordered]
    return _merge_evidence(chosen.evidence, evidences)


def _merge_evidence(chosen: Evidence, evidences: Sequence[Evidence]) -> Evidence:
    """Keep the selected passage while unioning assertion provenance."""
    metadata = dict(chosen.metadata)
    relation_ids = []
    claim_ids = []
    assertions = []
    for evidence in evidences:
        value = evidence.metadata.get("relation_source_ids", ())
        relation_ids.extend(value if isinstance(value, (tuple, list)) else ())
        value = evidence.metadata.get("claim_source_ids", ())
        claim_ids.extend(value if isinstance(value, (tuple, list)) else ())
        value = evidence.metadata.get("assertions", ())
        if isinstance(value, (tuple, list)):
            assertions.extend(value)
        for key in ("claim_status", "confidence", "claim_id", "claim_path", "property", "value", "relation"):
            if metadata.get(key) is None and evidence.metadata.get(key) is not None:
                metadata[key] = evidence.metadata[key]
        if evidence.metadata.get("requires_hedging"):
            metadata["requires_hedging"] = True
    if relation_ids or claim_ids:
        metadata["relation_source_ids"] = tuple(dict.fromkeys(relation_ids + claim_ids))
        metadata["claim_source_ids"] = tuple(dict.fromkeys(claim_ids))
    if assertions:
        metadata["assertions"] = tuple(assertions)
    return Evidence(
        entity_path=chosen.entity_path,
        section=chosen.section,
        text=chosen.text,
        source_ids=chosen.source_ids,
        passage_source_ids=chosen.passage_source_ids,
        metadata=metadata,
    )


def _snapshot_fallback(snapshot: Snapshot, path: str) -> Evidence | None:
    entity = next((item for item in snapshot.entities if item.entity_path == path), None)
    if entity is None:
        return None
    chunks = sorted((chunk for chunk in snapshot.chunks if chunk.entity_path == path), key=lambda chunk: (chunk.ordinal, chunk.chunk_id))
    if chunks:
        chunk = chunks[0]
        source_ids = tuple(normalize_source_id(value, allow_empty=True) for value in chunk.source_ids)
        return Evidence(path, chunk.text, chunk.heading, source_ids, {"chunk_id": chunk.chunk_id, "content_hash": chunk.content_hash}, source_ids)
    source_ids = tuple(normalize_source_id(value, allow_empty=True) for value in entity.source_ids)
    return Evidence(path, entity.content.strip() or (entity.description or entity.title), None, source_ids, {}, source_ids)


__all__ = ["FusionError", "RRFConfig", "fuse_entity_rankings"]
