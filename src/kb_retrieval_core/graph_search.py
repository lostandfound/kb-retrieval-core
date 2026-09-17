"""Deterministic selective one-hop graph expansion."""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass
from typing import Iterable

from ._normalization import normalize_source_id
from .models import Claim, Entity, Evidence, Relation, SearchHit, Snapshot

CONFIDENCE_WEIGHTS: dict[str, float] = {"A": 1.0, "B": 0.75, "C": 0.5, "D": 0.25}
DEFAULT_CLAIM_CONFIDENCE = 0.5


@dataclass(frozen=True, slots=True)
class _Edge:
    source: str
    target: str
    predicate: str
    relation_source_ids: tuple[str, ...] = ()
    relation: Relation | None = None
    claim: Claim | None = None

    @property
    def fingerprint(self) -> tuple[object, ...]:
        claim = self.claim
        return (
            self.source, self.target, self.predicate, self.relation_source_ids,
            None if claim is None else claim.claim_path,
            None if claim is None else claim.claim_id,
            None if claim is None else claim.status,
            None if claim is None else claim.confidence,
        )


class GraphIndex:
    """Immutable-in-practice adjacency index over a loaded Snapshot."""

    def __init__(self, snapshot: Snapshot, *, decay: float = 0.75, claim_decay: float = 0.5, confidence_weights: dict[str, float] | None = None) -> None:
        if not isinstance(snapshot, Snapshot):
            raise TypeError("snapshot must be a Snapshot")
        _validate_decay(decay, "decay")
        _validate_decay(claim_decay, "claim_decay")
        self.snapshot = snapshot
        self.decay = float(decay)
        self.claim_decay = float(claim_decay)
        weights = CONFIDENCE_WEIGHTS if confidence_weights is None else confidence_weights
        if not isinstance(weights, dict) or any(not isinstance(key, str) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0 for key, value in weights.items()):
            raise ValueError("confidence_weights must map strings to non-negative finite numbers")
        self.confidence_weights = dict(weights)
        self._entities = {entity.entity_path: entity for entity in snapshot.entities}
        edges: dict[tuple[object, ...], _Edge] = {}
        for entity in snapshot.entities:
            for relation in entity.relations:
                self._add_relation(edges, entity, relation)
            for claim in entity.claims:
                self._add_claim(edges, claim, entity.entity_path)
        for relation in snapshot.relations:
            if relation.source_path is not None:
                owner = self._entities.get(relation.source_path)
                self._add_relation(edges, owner, relation)
        for claim in snapshot.claims:
            self._add_claim(edges, claim, claim.subject)
        self._edges = tuple(
            edge for edge in sorted(edges.values(), key=lambda item: (item.source, item.target, item.predicate, item.claim is not None, item.fingerprint))
            if edge.source in self._entities and edge.target in self._entities
        )
        self._outgoing: dict[str, tuple[_Edge, ...]] = {}
        self._incoming: dict[str, tuple[_Edge, ...]] = {}
        for edge in self._edges:
            self._outgoing[edge.source] = self._outgoing.get(edge.source, ()) + (edge,)
            self._incoming[edge.target] = self._incoming.get(edge.target, ()) + (edge,)

    def _add_relation(self, edges: dict[tuple[object, ...], _Edge], owner: Entity | None, relation: Relation) -> None:
        if relation.source_path is not None:
            source = relation.source_path
        elif owner is not None:
            source = owner.entity_path
        else:
            return
        owner_ids = relation.owner_source_ids or (() if owner is None else owner.source_ids)
        support_ids = tuple(dict.fromkeys(normalize_source_id(item, allow_empty=True) for item in (relation.source_ids or owner_ids)))
        edge = _Edge(source, relation.target, relation.predicate, support_ids, relation=relation)
        key = (source, relation.target, relation.predicate, relation.confidence, False)
        previous = edges.get(key)
        if previous is None:
            edges[key] = edge
        else:
            edges[key] = _Edge(source, relation.target, relation.predicate, tuple(dict.fromkeys(previous.relation_source_ids + support_ids)), relation=relation)

    def _add_claim(self, edges: dict[tuple[object, ...], _Edge], claim: Claim, default_source: str | None) -> None:
        source = claim.subject or default_source
        if source is None:
            return
        if claim.predicate is not None and claim.target is not None:
            target = claim.target
            predicate = claim.predicate
        elif claim.property is not None:
            # Value Claims are assertion records, not graph relationships. A
            # subject self-target lets the expansion stage emit their evidence
            # without inventing a node or a relationship to the scalar value.
            target = source
            predicate = claim.property
        else:
            return
        edges.setdefault((source, target, predicate, claim.claim_path or claim.claim_id, True), _Edge(source, target, predicate, tuple(normalize_source_id(item, allow_empty=True) for item in claim.source_ids), claim=claim))

    @classmethod
    def from_snapshot(cls, snapshot: Snapshot, *, decay: float = 0.75, claim_decay: float = 0.5, confidence_weights: dict[str, float] | None = None) -> "GraphIndex":
        return cls(snapshot, decay=decay, claim_decay=claim_decay, confidence_weights=confidence_weights)

    def expand(self, seed_hits: Iterable[SearchHit], *, expand: bool = False, predicates: Iterable[str] | None = None, include_rejected: bool = False, include_unknown: bool = False, top_k: int | None = None) -> tuple[SearchHit, ...]:
        seeds = tuple(seed_hits)
        _validate_seeds(seeds, self._entities)
        if not isinstance(expand, bool):
            raise TypeError("expand must be boolean")
        _validate_bool(include_rejected, "include_rejected")
        _validate_bool(include_unknown, "include_unknown")
        if top_k is not None:
            _validate_top_k(top_k)
        predicate_set = _predicate_set(predicates)
        if not expand:
            return _rank_hits(seeds, top_k)
        candidates: list[tuple[float, str, str, SearchHit]] = [(hit.score, hit.evidence.entity_path, "", hit) for hit in seeds]
        seen: set[tuple[object, ...]] = set()
        for seed in sorted(seeds, key=lambda hit: (hit.rank, hit.evidence.entity_path, -hit.score)):
            seed_path = seed.evidence.entity_path
            for edge in self._outgoing.get(seed_path, ()) + self._incoming.get(seed_path, ()):
                if predicate_set is not None and _normalize(edge.predicate) not in predicate_set:
                    continue
                neighbor, direction = _neighbor(edge, seed_path)
                if neighbor is None:
                    continue
                status, factor, affirmative, confidence_factor = self._edge_policy(edge, include_rejected, include_unknown)
                if factor is None:
                    continue
                key = (neighbor, edge.fingerprint, direction)
                if key in seen:
                    continue
                seen.add(key)
                score = seed.score * self.decay * factor
                hit = _expanded_hit(self._entities[neighbor], edge, direction, seed, score, status, affirmative, confidence_factor)
                candidates.append((score, neighbor, f"{direction}:{edge.predicate}:{edge.fingerprint}", hit))
        ordered = sorted(candidates, key=lambda item: (-item[0], item[1], item[2]))
        if top_k is not None:
            ordered = ordered[:top_k]
        return _rank_hits((item[3] for item in ordered), len(ordered))

    def _edge_policy(self, edge: _Edge, include_rejected: bool, include_unknown: bool) -> tuple[str | None, float | None, bool | None, float | None]:
        if edge.claim is None:
            confidence = edge.relation.confidence if edge.relation is not None else None
            confidence_factor = 1.0 if confidence is None else self.confidence_weights.get(confidence, DEFAULT_CLAIM_CONFIDENCE)
            return None, confidence_factor, True, confidence_factor
        claim = edge.claim
        status = claim.status.casefold() if isinstance(claim.status, str) else None
        if status == "rejected" and not include_rejected:
            return status, None, False, None
        if status not in {"accepted", "proposed", "disputed", "rejected"} and not include_unknown:
            return status, None, False, None
        status_factor = 1.0 if status == "accepted" else self.claim_decay
        confidence_factor = self.confidence_weights.get(claim.confidence, DEFAULT_CLAIM_CONFIDENCE)
        return status, status_factor * confidence_factor, status == "accepted", confidence_factor

    def expand_hits(self, seed_hits: Iterable[SearchHit], **options: object) -> tuple[SearchHit, ...]:
        return self.expand(seed_hits, **options)  # type: ignore[arg-type]


def _neighbor(edge: _Edge, seed_path: str) -> tuple[str | None, str]:
    if edge.claim is not None and edge.claim.property is not None and edge.source == seed_path:
        return edge.source, "assertion"
    if edge.source == seed_path:
        return edge.target, "outgoing"
    if edge.target == seed_path:
        return edge.source, "incoming"
    return None, ""


def _expanded_hit(entity: Entity, edge: _Edge, direction: str, seed: SearchHit, score: float, status: str | None, affirmative: bool | None, confidence_factor: float | None) -> SearchHit:
    claim = edge.claim
    relation = edge.relation
    relation_metadata = {
        "predicate": edge.predicate,
        "direction": direction,
        "source_path": edge.source,
        "target_path": edge.target,
        "relation_source_ids": edge.relation_source_ids,
        "confidence": None if relation is None else relation.confidence,
        "confidence_weight": confidence_factor,
        "requires_hedging": bool(relation is not None and relation.confidence in {"C", "D"}),
    }
    metadata: dict[str, object] = {
        "result_type": "graph",
        "entity_path": entity.entity_path,
        "entity_type": entity.entity_type,
        "title": entity.title,
        "source_hit": {"entity_path": seed.evidence.entity_path, "rank": seed.rank, "score": seed.score},
        "relation": relation_metadata,
        "relation_source_ids": edge.relation_source_ids,
        "claim_status": status,
        "confidence": claim.confidence if claim is not None else (None if relation is None else relation.confidence),
        "confidence_weight": confidence_factor,
        "claim_id": None if claim is None else claim.claim_id,
        "claim_path": None if claim is None else claim.claim_path,
        "claim_source_ids": () if claim is None else tuple(normalize_source_id(item, allow_empty=True) for item in claim.source_ids),
        "affirmative": affirmative,
        "requires_hedging": bool(claim is not None and claim.confidence in {"C", "D"}) or bool(relation is not None and relation.confidence in {"C", "D"}),
    }
    if claim is not None:
        metadata["property"] = claim.property
        metadata["value"] = claim.value
    text = entity.content.strip() or (entity.description or entity.title)
    evidence = Evidence(
        entity_path=entity.entity_path,
        text=text,
        source_ids=tuple(
            normalize_source_id(item, allow_empty=True) for item in entity.source_ids
        ),
        metadata=metadata,
    )
    return SearchHit(
        evidence=evidence,
        score=score,
        rank=1,
        retriever="graph.one-hop",
    )


def _validate_seeds(seeds: tuple[SearchHit, ...], entities: dict[str, Entity]) -> None:
    for seed in seeds:
        if not isinstance(seed, SearchHit):
            raise TypeError("seed_hits must contain SearchHit objects")
        if seed.evidence.entity_path not in entities:
            raise ValueError(f"seed evidence refers to unknown entity path: {seed.evidence.entity_path}")


def _validate_decay(value: float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or not 0 < value <= 1:
        raise ValueError(f"{name} must be between 0 and 1")


def _validate_bool(value: bool, name: str) -> None:
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be boolean")


def _validate_top_k(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("top_k must be a positive integer")


def _predicate_set(predicates: Iterable[str] | None) -> frozenset[str] | None:
    if predicates is None:
        return None
    if isinstance(predicates, str):
        predicates = (predicates,)
    values = tuple(predicates)
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError("predicates must contain non-empty strings")
    return frozenset(_normalize(value) for value in values)


def _normalize(value: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFKC", value).casefold() if not char.isspace())


def _rank_hits(hits: Iterable[SearchHit], top_k: int | None = None) -> tuple[SearchHit, ...]:
    ordered = sorted(tuple(hits), key=lambda hit: (-hit.score, hit.evidence.entity_path, hit.retriever, hit.rank))
    if top_k is not None:
        ordered = ordered[:top_k]
    return tuple(SearchHit(hit.evidence, hit.score, i, hit.retriever) for i, hit in enumerate(ordered, 1))


class GraphSearcher(GraphIndex):
    """Compatibility name for the graph index/searcher."""


def build_graph_index(snapshot: Snapshot, *, decay: float = 0.75, claim_decay: float = 0.5, confidence_weights: dict[str, float] | None = None) -> GraphIndex:
    return GraphIndex(snapshot, decay=decay, claim_decay=claim_decay, confidence_weights=confidence_weights)


__all__ = ["CONFIDENCE_WEIGHTS", "DEFAULT_CLAIM_CONFIDENCE", "GraphIndex", "GraphSearcher", "build_graph_index"]
