import pytest

from kb_retrieval_core import (
    Entity,
    Evidence,
    HybridRetriever,
    RRFConfig,
    RetrievalConfig,
    RetrievalError,
    SearchHit,
    Snapshot,
)


class StubVector:
    def __init__(self, hits):
        self.hits = tuple(hits)
        self.calls = 0

    def search(self, query, *, top_k):
        self.calls += 1
        return self.hits[:top_k]


def _snapshot():
    return Snapshot(entities=(
        Entity("/a.md", "Term", "Alpha", source_ids=("a",), content="# Intro\nAlpha passage"),
        Entity("/b.md", "Term", "Beta", source_ids=("b",), content="# Intro\nBeta passage"),
    ))


def _vector_hit(path, rank=1):
    return SearchHit(Evidence(path, "vector evidence", "Intro", ("vector",), {"chunk_id": path + "#Intro"}, ("vector",)), 1.0, rank, "vector.chunk")


def test_lexical_mode_is_default_and_does_not_require_vector():
    retriever = HybridRetriever(_snapshot())
    hits = retriever.search("Alpha")
    assert hits
    assert hits[0].retriever == "lexical.chunk"


def test_vector_mode_requires_explicit_vector_resource():
    with pytest.raises(RetrievalError, match="requires"):
        HybridRetriever(_snapshot()).search("Alpha", mode="vector")


def test_incompatible_vector_resource_uses_actionable_error():
    with pytest.raises(RetrievalError, match="requires"):
        HybridRetriever(_snapshot(), vector=object()).search("Alpha", mode="vector")


def test_hybrid_invokes_each_backend_once_and_fuses_results():
    vector = StubVector((_vector_hit("/b.md"), _vector_hit("/a.md", rank=2)))
    retriever = HybridRetriever(_snapshot(), vector=vector)
    hits = retriever.search("Alpha", config=RetrievalConfig(mode="hybrid", top_k=2, rrf=RRFConfig(constant=1.0)))
    assert vector.calls == 1
    assert len(hits) == 2
    assert all(hit.retriever == "rrf.entity" for hit in hits)


def test_requested_mode_is_not_silently_changed():
    with pytest.raises(ValueError, match="conflicts"):
        HybridRetriever(_snapshot()).search("Alpha", config=RetrievalConfig(mode="lexical"), mode="vector")


def test_orchestration_cutoffs_are_recorded_in_fusion_metadata():
    vector = StubVector((_vector_hit("/b.md"),))
    config = RetrievalConfig(
        mode="hybrid",
        top_k=1,
        backend_cutoffs={"vector": 1},
        rrf=RRFConfig(constant=2.0, backend_weights={"vector": 3.0}),
    )
    result = HybridRetriever(_snapshot(), vector=vector).search("Alpha", config=config)
    assert result[0].evidence.metadata["rrf"]["backend_cutoffs"] == {"vector": 1}
