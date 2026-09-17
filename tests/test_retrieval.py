import pytest

from kb_retrieval_core import (
    Claim,
    Entity,
    Evidence,
    GraphExpansionConfig,
    HybridRetriever,
    RRFConfig,
    RetrievalConfig,
    RetrievalError,
    Relation,
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


def test_graph_expansion_is_connected_but_default_disabled():
    snapshot = Snapshot(entities=(
        Entity("/a.md", "Person", "Alpha", source_ids=("a",), content="Alpha", relations=(Relation("teaches", "/b.md", source_path="/a.md"),)),
        Entity("/b.md", "Person", "Beta", source_ids=("b",), content="Beta"),
    ))
    retriever = HybridRetriever(snapshot)
    assert {hit.evidence.entity_path for hit in retriever.search("Alpha", top_k=2)} == {"/a.md"}
    hits = retriever.search("Alpha", config=RetrievalConfig(top_k=2, graph=GraphExpansionConfig(enabled=True)))
    assert {hit.evidence.entity_path for hit in hits} == {"/a.md", "/b.md"}
    expanded = next(hit for hit in hits if hit.evidence.entity_path == "/b.md")
    assert expanded.evidence.metadata["relation"]["direction"] == "outgoing"
    assert expanded.evidence.metadata["graph_expansion"]["enabled"] is True


def test_graph_expansion_preserves_incoming_claim_path():
    claim = Claim(
        claim_path="/claims/teaching.md", subject="/a.md", predicate="teaches",
        target="/b.md", status="accepted", confidence="B", source_ids=("claim-source",),
    )
    snapshot = Snapshot(entities=(
        Entity("/a.md", "Person", "Alpha", source_ids=("a",), content="Alpha"),
        Entity("/b.md", "Person", "Beta", source_ids=("b",), content="Beta"),
    ), claims=(claim,))
    hits = HybridRetriever(snapshot).search("Beta", config=RetrievalConfig(top_k=2, graph=GraphExpansionConfig(enabled=True)))
    expanded = next(hit for hit in hits if hit.evidence.entity_path == "/a.md")
    assert expanded.evidence.metadata["relation"]["direction"] == "incoming"
    assert expanded.evidence.metadata["claim_path"] == "/claims/teaching.md"


def test_graph_expansion_deduplicates_chunk_seeds_and_reserves_expanded_result() -> None:
    snapshot = Snapshot(entities=(
        Entity(
            "/a.md", "Person", "Alpha", source_ids=("a",),
            content="# One\nAlpha first\n# Two\nAlpha second\n# Three\nAlpha third",
            relations=(Relation("teaches", "/b.md", source_path="/a.md"),),
        ),
        Entity("/b.md", "Person", "Teacher", source_ids=("b",), content="# Bio\nTeacher"),
        Entity("/c.md", "Person", "Alpha unrelated", source_ids=("c",), content="# Bio\nAlpha elsewhere"),
    ))
    hits = HybridRetriever(snapshot).search(
        "Alpha",
        config=RetrievalConfig(top_k=2, graph=GraphExpansionConfig(enabled=True)),
    )
    assert len([hit for hit in hits if hit.evidence.entity_path == "/a.md" and hit.retriever != "graph.one-hop"]) == 1
    assert any(hit.evidence.entity_path == "/b.md" and hit.retriever == "graph.one-hop" for hit in hits)
    assert all(hit.evidence.metadata["graph_expansion"]["seed_pool_factor"] == 4 for hit in hits)


def test_graph_expansion_pool_and_result_limits_validate() -> None:
    with pytest.raises(ValueError, match="seed_pool_factor"):
        GraphExpansionConfig(enabled=True, seed_pool_factor=0)
    with pytest.raises(ValueError, match="expanded_result_limit"):
        GraphExpansionConfig(enabled=True, expanded_result_limit=0)


def test_graph_reservation_prefers_same_type_relations_without_predicate_policy() -> None:
    snapshot = Snapshot(entities=(
        Entity(
            "/seed.md", "Person", "Seed", content="# Bio\nSeed query",
            relations=(
                Relation("related", "/history.md", source_path="/seed.md"),
                Relation("taught", "/teacher.md", source_path="/seed.md"),
            ),
        ),
        Entity("/history.md", "Event", "History", content="History"),
        Entity("/teacher.md", "Person", "Teacher", content="Teacher"),
    ))
    hits = HybridRetriever(snapshot).search(
        "Seed query",
        config=RetrievalConfig(top_k=2, graph=GraphExpansionConfig(enabled=True)),
    )
    assert any(hit.evidence.entity_path == "/teacher.md" for hit in hits)
    assert not any(hit.evidence.entity_path == "/history.md" for hit in hits)
