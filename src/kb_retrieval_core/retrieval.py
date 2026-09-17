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

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise TypeError("enabled must be boolean")
        if not isinstance(self.include_rejected, bool) or not isinstance(self.include_unknown, bool):
            raise TypeError("graph Claim policy flags must be boolean")
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
        cutoffs = dict(selected.backend_cutoffs or {})
        default_cutoff = selected.top_k
        if selected.mode == "lexical":
            direct = self.lexical.search_chunks(query, top_k=cutoffs.get("lexical.passage", default_cutoff))
            return self._expand(direct, selected)
        if self.vector is None or not callable(getattr(self.vector, "search", None)):
            raise RetrievalError(f"retrieval mode {selected.mode!r} requires a vector retriever")
        if selected.mode == "vector":
            direct = self.vector.search(query, top_k=cutoffs.get("vector", default_cutoff))
            return self._expand(direct, selected)
        lexical_chunks = self.lexical.search_chunks(query, top_k=cutoffs.get("lexical.passage", default_cutoff))
        lexical_entities = self.lexical.search_entities(query, top_k=cutoffs.get("lexical.entity", default_cutoff))
        vector_hits = self.vector.search(query, top_k=cutoffs.get("vector", default_cutoff))
        if selected.rrf is None:
            rrf = RRFConfig(backend_cutoffs=cutoffs)
        else:
            effective_cutoffs = dict(selected.rrf.backend_cutoffs)
            effective_cutoffs.update(cutoffs)
            rrf = RRFConfig(
                constant=selected.rrf.constant,
                backend_weights=selected.rrf.backend_weights,
                backend_cutoffs=effective_cutoffs,
                passage_precedence=selected.rrf.passage_precedence,
            )
        direct = fuse_entity_rankings({"lexical.passage": lexical_chunks, "lexical.entity": lexical_entities, "vector": vector_hits}, top_k=selected.top_k, config=rrf, snapshot=self.snapshot)
        return self._expand(direct, selected)

    def _expand(self, hits: tuple[SearchHit, ...], config: RetrievalConfig) -> tuple[SearchHit, ...]:
        policy = config.graph or GraphExpansionConfig()
        metadata = {
            "enabled": policy.enabled,
            "predicates": list(policy.predicates) if policy.predicates is not None else None,
            "include_rejected": policy.include_rejected,
            "include_unknown": policy.include_unknown,
            "decay": self.graph.decay,
            "claim_decay": self.graph.claim_decay,
            "confidence_weights": dict(sorted(self.graph.confidence_weights.items())),
        }
        expanded = self.graph.expand(
            hits,
            expand=policy.enabled,
            predicates=policy.predicates,
            include_rejected=policy.include_rejected,
            include_unknown=policy.include_unknown,
            top_k=config.top_k,
        )
        return tuple(_with_graph_policy(hit, metadata) for hit in expanded)


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


__all__ = ["GraphExpansionConfig", "HybridRetriever", "RetrievalConfig", "RetrievalError", "build_retriever"]
