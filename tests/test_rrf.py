from kb_retrieval_core import (
    Entity,
    Evidence,
    RRFConfig,
    SearchHit,
    Snapshot,
    chunk_snapshot,
    fuse_entity_rankings,
)


def _hit(path: str, rank: int, *, backend: str, chunk_id: str | None = None, text: str = "text") -> SearchHit:
    metadata = {} if chunk_id is None else {"chunk_id": chunk_id}
    return SearchHit(Evidence(path, text, "Section", ("source",), metadata, ("source",)), 100.0 - rank, rank, backend)


def test_rrf_collapses_chunks_and_ignores_backend_score_scales() -> None:
    result = fuse_entity_rankings({
        "lexical": (_hit("/a.md", 1, backend="lexical", chunk_id="/a.md#one"), _hit("/a.md", 2, backend="lexical", chunk_id="/a.md#two"), _hit("/b.md", 2, backend="lexical", chunk_id="/b.md#one")),
        "vector": (_hit("/b.md", 1, backend="vector", chunk_id="/b.md#one"), _hit("/a.md", 2, backend="vector", chunk_id="/a.md#one")),
    }, config=RRFConfig(constant=1.0), top_k=2)
    assert [hit.evidence.entity_path for hit in result] == ["/a.md", "/b.md"]
    assert result[0].evidence.metadata["chunk_id"] == "/a.md#one"
    assert result[0].evidence.metadata["rrf"]["backend_ranks"] == {"lexical": 1, "vector": 2}


def test_rrf_deterministic_tie_uses_entity_path() -> None:
    result = fuse_entity_rankings({"one": (_hit("/b.md", 1, backend="one"), _hit("/a.md", 1, backend="one"))}, config=RRFConfig(constant=60.0))
    assert [hit.evidence.entity_path for hit in result] == ["/a.md", "/b.md"]
    assert [hit.rank for hit in result] == [1, 2]


def test_rrf_entity_only_hit_falls_back_to_lowest_ordinal_chunk() -> None:
    snapshot = chunk_snapshot(Snapshot(entities=(Entity("/a.md", "Term", "A", source_ids=("ref:a",), content="# First\nfirst\n# Second\nsecond"),)))
    result = fuse_entity_rankings({"entity": (_hit("/a.md", 1, backend="entity"),)}, snapshot=snapshot)
    assert result[0].evidence.metadata["chunk_id"] == "/a.md#First"
    assert result[0].evidence.source_ids == ("a",)


def test_rrf_preserves_selected_passage_provenance() -> None:
    hit = _hit("/a.md", 1, backend="vector", chunk_id="/a.md#one")
    result = fuse_entity_rankings({"vector": (hit,)})
    assert result[0].evidence.passage_source_ids == ("source",)
    assert result[0].evidence.metadata["chunk_id"] == "/a.md#one"


def test_rrf_records_complete_fusion_configuration() -> None:
    config = RRFConfig(
        constant=12.0,
        backend_weights={"vector": 2.0},
        backend_cutoffs={"vector": 3},
        passage_precedence=("vector", "lexical"),
    )
    result = fuse_entity_rankings({"vector": (_hit("/a.md", 1, backend="vector", chunk_id="/a.md#one"),)}, config=config)
    fusion = result[0].evidence.metadata["rrf"]
    assert fusion["constant"] == 12.0
    assert fusion["backend_weights"] == {"vector": 2.0}
    assert fusion["backend_cutoffs"] == {"vector": 3}
    assert fusion["passage_precedence"] == ("vector", "lexical")
    assert fusion["backend_ranks"] == {"vector": 1}
    assert fusion["entity_path"] == "/a.md"
