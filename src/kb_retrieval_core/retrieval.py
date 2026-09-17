"""Explicit lexical, vector, and hybrid retrieval orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .lexical import LexicalIndex
from .graph_search import GraphIndex
from .models import Evidence, SearchHit, Snapshot
from .rrf import RRFConfig, fuse_entity_rankings


class RetrievalError(ValueError):
    """Raised when a requested retrieval mode is unavailable or invalid."""


@dataclass(frozen=True, slots=True)
class GraphExpansionConfig:
    """Explicit, default-disabled one-hop expansion policy."""

    enabled: bool = False
    predicates: tuple[str, ...] | None = None
    include_rejected: bool = False
    include_unknown: bool = False
    seed_pool_factor: int = 4
    expanded_result_limit: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise TypeError("enabled must be boolean")
        if not isinstance(self.include_rejected, bool) or not isinstance(self.include_unknown, bool):
            raise TypeError("graph Claim policy flags must be boolean")
        if isinstance(self.seed_pool_factor, bool) or not isinstance(self.seed_pool_factor, int) or self.seed_pool_factor < 1:
            raise ValueError("seed_pool_factor must be a positive integer")
        if self.expanded_result_limit is not None and (
            isinstance(self.expanded_result_limit, bool)
            or not isinstance(self.expanded_result_limit, int)
            or self.expanded_result_limit < 1
        ):
            raise ValueError("expanded_result_limit must be a positive integer or None")
        if self.predicates is not None:
            predicates = tuple(self.predicates)
            if any(not isinstance(item, str) or not item.strip() for item in predicates):
                raise ValueError("predicates must contain non-empty strings")
            object.__setattr__(self, "predicates", predicates)


@dataclass(frozen=True, slots=True)
class RetrievalConfig:
    """Mode and deterministic backend cutoffs for orchestration."""

    mode: str = "lexical"
    top_k: int = 5
    backend_cutoffs: Mapping[str, int] | None = None
    rrf: RRFConfig | None = None
    graph: GraphExpansionConfig | None = None

    def __post_init__(self) -> None:
        if self.mode not in {"lexical", "vector", "hybrid"}:
            raise ValueError("mode must be 'lexical', 'vector', or 'hybrid'")
        if isinstance(self.top_k, bool) or not isinstance(self.top_k, int) or self.top_k < 1:
            raise ValueError("top_k must be a positive integer")
        if self.backend_cutoffs is not None:
            cutoffs = dict(self.backend_cutoffs)
            if any(not isinstance(name, str) or not name.strip() for name in cutoffs):
                raise ValueError("backend cutoff names must be non-empty strings")
            if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in cutoffs.values()):
                raise ValueError("backend cutoffs must be positive integers")
            object.__setattr__(self, "backend_cutoffs", cutoffs)
        if self.rrf is not None and not isinstance(self.rrf, RRFConfig):
            raise TypeError("rrf must be an RRFConfig")
        if self.graph is not None and not isinstance(self.graph, GraphExpansionConfig):
            raise TypeError("graph must be a GraphExpansionConfig")

    def as_dict(self) -> dict[str, object]:
        """Return the effective, reproducible orchestration policy."""
        graph = self.graph or GraphExpansionConfig()
        cutoffs = effective_backend_cutoffs(self)
        return {
            "mode": self.mode,
            "top_k": self.top_k,
            "backend_cutoffs": dict(sorted(cutoffs.items())),
            "rrf": None if self.rrf is None else self.rrf.as_dict(),
            "graph": {
                "enabled": graph.enabled,
                "predicates": list(graph.predicates) if graph.predicates is not None else None,
                "include_rejected": graph.include_rejected,
                "include_unknown": graph.include_unknown,
                "seed_pool_factor": graph.seed_pool_factor,
                "effective_seed_cutoff": _seed_cutoff(self),
                "seed_deduplication": "entity_path:first-ranked",
                "expanded_result_limit": graph.expanded_result_limit or max(1, (self.top_k + 1) // 2),
                "reservation_policy": "graph-first-with-direct-fill" if graph.enabled else "disabled",
                "selection_policy": "same-entity-type-relation-then-unique-claims" if graph.enabled else "disabled",
                "decay": 0.75,
                "claim_decay": 0.5,
                "confidence_weights": {"A": 1.0, "B": 0.75, "C": 0.5, "D": 0.25},
            },
        }


class HybridRetriever:
    """Run only the backends required by the explicitly requested mode."""

    def __init__(self, snapshot: Snapshot, *, lexical: LexicalIndex | None = None, vector: object | None = None, graph: GraphIndex | None = None) -> None:
        if not isinstance(snapshot, Snapshot):
            raise TypeError("snapshot must be a Snapshot")
        self.snapshot = snapshot
        self.lexical = lexical or LexicalIndex(snapshot)
        self.vector = vector
        self.graph = graph or GraphIndex(snapshot)

    def search(self, query: str, *, config: RetrievalConfig | None = None, mode: str | None = None, top_k: int | None = None) -> tuple[SearchHit, ...]:
        selected = config or RetrievalConfig(mode=mode or "lexical", top_k=top_k or 5)
        if mode is not None and mode != selected.mode:
            raise ValueError("mode conflicts with config.mode")
        if top_k is not None and top_k != selected.top_k:
            selected = RetrievalConfig(selected.mode, top_k, selected.backend_cutoffs, selected.rrf, selected.graph)
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must not be empty")
        cutoffs = effective_backend_cutoffs(selected)
        configured_cutoffs = dict(selected.backend_cutoffs or {})
        seed_cutoff = _seed_cutoff(selected)
        if selected.mode == "lexical":
            direct = self.lexical.search_chunks(query, top_k=cutoffs.get("lexical.passage", seed_cutoff))
            return self._expand(direct, selected)
        if self.vector is None or not callable(getattr(self.vector, "search", None)):
            raise RetrievalError(f"retrieval mode {selected.mode!r} requires a vector retriever")
        if selected.mode == "vector":
            direct = self.vector.search(query, top_k=cutoffs.get("vector", seed_cutoff))
            return self._expand(direct, selected)
        lexical_chunks = self.lexical.search_chunks(query, top_k=cutoffs.get("lexical.passage", seed_cutoff))
        lexical_entities = self.lexical.search_entities(query, top_k=cutoffs.get("lexical.entity", seed_cutoff))
        vector_hits = self.vector.search(query, top_k=cutoffs.get("vector", seed_cutoff))
        if selected.rrf is None:
            rrf = RRFConfig(backend_cutoffs=configured_cutoffs)
        else:
            effective_cutoffs = dict(selected.rrf.backend_cutoffs)
            effective_cutoffs.update(configured_cutoffs)
            rrf = RRFConfig(
                constant=selected.rrf.constant,
                backend_weights=selected.rrf.backend_weights,
                backend_cutoffs=effective_cutoffs,
                passage_precedence=selected.rrf.passage_precedence,
            )
        direct = fuse_entity_rankings({"lexical.passage": lexical_chunks, "lexical.entity": lexical_entities, "vector": vector_hits}, top_k=seed_cutoff, config=rrf, snapshot=self.snapshot)
        return self._expand(direct, selected)

    def _expand(self, hits: tuple[SearchHit, ...], config: RetrievalConfig) -> tuple[SearchHit, ...]:
        policy = config.graph or GraphExpansionConfig()
        seeds = _deduplicate_seed_entities(hits) if policy.enabled else hits
        expanded_limit = policy.expanded_result_limit or max(1, (config.top_k + 1) // 2)
        metadata = {
            "enabled": policy.enabled,
            "predicates": list(policy.predicates) if policy.predicates is not None else None,
            "include_rejected": policy.include_rejected,
            "include_unknown": policy.include_unknown,
            "decay": self.graph.decay,
            "claim_decay": self.graph.claim_decay,
            "confidence_weights": dict(sorted(self.graph.confidence_weights.items())),
            "seed_pool_factor": policy.seed_pool_factor,
            "seed_count": len(seeds),
            "expanded_result_limit": expanded_limit,
        }
        expanded = self.graph.expand(
            seeds,
            expand=policy.enabled,
            predicates=policy.predicates,
            include_rejected=policy.include_rejected,
            include_unknown=policy.include_unknown,
            top_k=None if policy.enabled else config.top_k,
        )
        if policy.enabled:
            expanded = _select_diverse_results(expanded, top_k=config.top_k, expanded_limit=expanded_limit)
        return tuple(_with_graph_policy(hit, metadata) for hit in expanded)


def _seed_cutoff(config: RetrievalConfig) -> int:
    policy = config.graph or GraphExpansionConfig()
    return config.top_k * policy.seed_pool_factor if policy.enabled else config.top_k


def effective_backend_cutoffs(config: RetrievalConfig) -> dict[str, int]:
    """Cutoffs actually passed to each backend for ``config``."""
    seed_cutoff = _seed_cutoff(config)
    explicit = dict(config.backend_cutoffs or {})
    if config.mode == "lexical":
        names = ("lexical.passage",)
    elif config.mode == "vector":
        names = ("vector",)
    else:
        names = ("lexical.passage", "lexical.entity", "vector")
    return {name: explicit.get(name, seed_cutoff) for name in names}


def _deduplicate_seed_entities(hits: tuple[SearchHit, ...]) -> tuple[SearchHit, ...]:
    selected: list[SearchHit] = []
    seen: set[str] = set()
    for hit in sorted(hits, key=lambda item: (item.rank, -item.score, item.evidence.entity_path)):
        if hit.evidence.entity_path in seen:
            continue
        seen.add(hit.evidence.entity_path)
        selected.append(SearchHit(hit.evidence, hit.score, len(selected) + 1, hit.retriever))
    return tuple(selected)


def _select_diverse_results(
    hits: tuple[SearchHit, ...], *, top_k: int, expanded_limit: int
) -> tuple[SearchHit, ...]:
    graph_hits = [hit for hit in hits if hit.retriever == "graph.one-hop"]
    relation_hits = [hit for hit in graph_hits if hit.evidence.metadata.get("claim_path") is None]
    relation_hits.sort(
        key=lambda hit: (
            not bool(hit.evidence.metadata.get("same_entity_type")),
            -hit.score,
            hit.evidence.entity_path,
            hit.rank,
        )
    )
    claim_hits: list[SearchHit] = []
    claim_paths: set[str] = set()
    for hit in graph_hits:
        claim_path = hit.evidence.metadata.get("claim_path")
        if not isinstance(claim_path, str) or claim_path in claim_paths:
            continue
        claim_paths.add(claim_path)
        claim_hits.append(hit)
    graph_budget = min(expanded_limit, top_k)
    reserved = relation_hits[:1]
    reserved.extend(claim_hits[: graph_budget - len(reserved)])
    if len(reserved) < graph_budget:
        reserved_ids = {id(hit) for hit in reserved}
        reserved.extend(hit for hit in relation_hits if id(hit) not in reserved_ids)
        reserved = reserved[:graph_budget]
    direct_hits = [hit for hit in hits if hit.retriever != "graph.one-hop"]
    selected = reserved + direct_hits[: top_k - len(reserved)]
    ordered = sorted(selected, key=lambda item: (-item.score, item.evidence.entity_path, item.retriever, item.rank))
    return tuple(SearchHit(hit.evidence, hit.score, rank, hit.retriever) for rank, hit in enumerate(ordered, 1))


def _with_graph_policy(hit: SearchHit, policy: Mapping[str, object]) -> SearchHit:
    evidence = hit.evidence
    metadata = dict(evidence.metadata)
    metadata["graph_expansion"] = dict(policy)
    updated = Evidence(
        evidence.entity_path,
        evidence.text,
        evidence.section,
        evidence.source_ids,
        metadata,
        evidence.passage_source_ids,
    )
    return SearchHit(updated, hit.score, hit.rank, hit.retriever)


def build_retriever(snapshot: Snapshot, *, lexical: LexicalIndex | None = None, vector: object | None = None, graph: GraphIndex | None = None) -> HybridRetriever:
    return HybridRetriever(snapshot, lexical=lexical, vector=vector, graph=graph)


__all__ = ["GraphExpansionConfig", "HybridRetriever", "RetrievalConfig", "RetrievalError", "build_retriever", "effective_backend_cutoffs"]
