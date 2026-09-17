from __future__ import annotations

from pathlib import Path

import pytest

from kb_retrieval_core import (
    Claim,
    Entity,
    Reference,
    Relation,
    EmbeddingConfig,
    InjectedEmbedder,
    Snapshot,
    SQLiteVectorSidecar,
    VectorIndexConfig,
    VectorRecord,
    VectorRetrievalError,
    VectorRetriever,
    assemble_context,
    chunk_snapshot,
)


HASH_A = "a" * 64
HASH_B = "b" * 64


def _config() -> EmbeddingConfig:
    return EmbeddingConfig("test", "retrieval", 3, revision="v1")


def _snapshot_and_records(metric: str = "cosine"):
    snapshot = chunk_snapshot(
        Snapshot(
            entities=(
                Entity(
                    "/a.md",
                    "Term",
                    "A",
                    source_ids=("source-a",),
                    content="# Intro\nAlpha passage",
                ),
                Entity(
                    "/b.md",
                    "Term",
                    "B",
                    source_ids=("source-b",),
                    content="# Intro\nBeta passage",
                ),
            )
        )
    )
    vectors = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
    records = tuple(
        VectorRecord(chunk.chunk_id, chunk.content_hash, vector)
        for chunk, vector in zip(snapshot.chunks, vectors)
    )
    return snapshot, records


def _embedder(query_vector=(1.0, 0.0, 0.0), *, config=None):
    selected = _config() if config is None else config
    return InjectedEmbedder(
        selected,
        document_embedder=lambda documents: (),
        query_embedder=lambda _query: query_vector,
    )


def _build(tmp_path: Path, *, metric: str = "cosine", query_vector=(1.0, 0.0, 0.0)):
    snapshot, records = _snapshot_and_records(metric)
    config = VectorIndexConfig(_config(), metric, 1)
    sidecar = SQLiteVectorSidecar.build(
        records,
        tmp_path / "vectors",
        config=config,
        snapshot_hash=HASH_A,
        chunk_hash=HASH_B,
    )
    retriever = VectorRetriever(
        snapshot,
        sidecar,
        _embedder(query_vector, config=config.embedding),
        snapshot_hash=HASH_A,
        chunk_hash=HASH_B,
    )
    return snapshot, sidecar, retriever


def test_vector_retrieval_returns_deterministic_ranked_evidence(tmp_path: Path) -> None:
    _snapshot, sidecar, retriever = _build(tmp_path)
    hits = retriever.search("question", top_k=2)
    assert [hit.rank for hit in hits] == [1, 2]
    assert [hit.evidence.entity_path for hit in hits] == ["/a.md", "/b.md"]
    assert hits[0].score == pytest.approx(1.0)
    assert hits[0].evidence.section == "Intro"
    assert hits[0].evidence.source_ids == ("source-a",)
    assert hits[0].evidence.passage_source_ids == ("source-a",)
    assert hits[0].evidence.metadata["chunk_id"] == "/a.md#Intro"
    sidecar.close()


def test_vector_retrieval_applies_cutoff_and_stable_tie_break(tmp_path: Path) -> None:
    _snapshot, sidecar, retriever = _build(tmp_path, query_vector=(1.0, 1.0, 0.0))
    hits = retriever.search("question", top_k=1)
    assert len(hits) == 1
    assert hits[0].evidence.entity_path == "/a.md"
    with pytest.raises(ValueError, match="top_k"):
        retriever.search("question", top_k=0)
    with pytest.raises(ValueError, match="top_k"):
        retriever.search("question", top_k=True)
    sidecar.close()


def test_dot_metric_is_supported_and_unknown_metric_fails_at_search(tmp_path: Path) -> None:
    _snapshot, sidecar, retriever = _build(tmp_path, metric="dot", query_vector=(2.0, 0.0, 0.0))
    hits = retriever.search("question", top_k=1)
    assert hits[0].score == pytest.approx(2.0)
    sidecar.close()

    snapshot, records = _snapshot_and_records("manhattan")
    config = VectorIndexConfig(_config(), "manhattan", 1)
    unknown_sidecar = SQLiteVectorSidecar.build(
        records,
        tmp_path / "unknown",
        config=config,
        snapshot_hash=HASH_A,
        chunk_hash=HASH_B,
    )
    unknown = VectorRetriever(
        snapshot,
        unknown_sidecar,
        _embedder(config=config.embedding),
        snapshot_hash=HASH_A,
        chunk_hash=HASH_B,
    )
    with pytest.raises(VectorRetrievalError, match="unsupported similarity"):
        unknown.search("question")
    unknown_sidecar.close()


@pytest.mark.parametrize("field", ["snapshot_hash", "chunk_hash"])
def test_retriever_rejects_snapshot_compatibility_mismatch(tmp_path: Path, field: str) -> None:
    snapshot, records = _snapshot_and_records()
    config = VectorIndexConfig(_config(), "cosine", 1)
    sidecar = SQLiteVectorSidecar.build(
        records,
        tmp_path / field,
        config=config,
        snapshot_hash=HASH_A,
        chunk_hash=HASH_B,
    )
    with pytest.raises(VectorRetrievalError, match="incompatible"):
        VectorRetriever(
            snapshot,
            sidecar,
            _embedder(config=config.embedding),
            snapshot_hash="c" * 64 if field == "snapshot_hash" else HASH_A,
            chunk_hash="c" * 64 if field == "chunk_hash" else HASH_B,
        )
    sidecar.close()


def test_retriever_rejects_sidecar_chunk_set_or_content_mismatch(tmp_path: Path) -> None:
    snapshot, records = _snapshot_and_records()
    config = VectorIndexConfig(_config(), "cosine", 1)
    missing = SQLiteVectorSidecar.build(
        records[:1],
        tmp_path / "missing",
        config=config,
        snapshot_hash=HASH_A,
        chunk_hash=HASH_B,
    )
    with pytest.raises(VectorRetrievalError, match="chunk IDs"):
        VectorRetriever(
            snapshot,
            missing,
            _embedder(config=config.embedding),
            snapshot_hash=HASH_A,
            chunk_hash=HASH_B,
        )
    missing.close()

    changed_records = (
        VectorRecord(records[0].chunk_id, "c" * 64, records[0].vector),
        records[1],
    )
    changed = SQLiteVectorSidecar.build(
        changed_records,
        tmp_path / "changed",
        config=config,
        snapshot_hash=HASH_A,
        chunk_hash=HASH_B,
    )
    with pytest.raises(VectorRetrievalError, match="content hash"):
        VectorRetriever(
            snapshot,
            changed,
            _embedder(config=config.embedding),
            snapshot_hash=HASH_A,
            chunk_hash=HASH_B,
        )
    changed.close()


def test_retriever_rejects_invalid_queries_and_preserves_vector_provider_errors(tmp_path: Path) -> None:
    _snapshot, sidecar, retriever = _build(tmp_path)
    with pytest.raises(ValueError, match="query"):
        retriever.search("  ")
    sidecar.close()


def test_vector_evidence_normalizes_passage_sources_and_preserves_relation_provenance(tmp_path: Path) -> None:
    relation = Relation("teaches", "/target.md", source_ids=("ref:relation",), confidence="C")
    claim = Claim(
        claim_id="claim-1",
        claim_path="/claims/teaching.md",
        subject="/source.md",
        predicate="teaches",
        target="/target.md",
        status="proposed",
        confidence="D",
        source_ids=("ref:claim",),
    )
    snapshot = chunk_snapshot(Snapshot(entities=(
        Entity("/source.md", "Term", "Source", source_ids=("ref:passage",), relations=(relation,), claims=(claim,), content="# Intro\nPassage"),
        Entity("/target.md", "Term", "Target", content="# Intro\nTarget"),
    ), references=(
        Reference("passage", title="Passage"), Reference("relation", title="Relation"), Reference("claim", title="Claim"),
    )))
    config = VectorIndexConfig(_config(), "cosine", 1)
    sidecar = SQLiteVectorSidecar.build(
        tuple(VectorRecord(chunk.chunk_id, chunk.content_hash, (1.0, 0.0, 0.0)) for chunk in snapshot.chunks),
        tmp_path / "vectors", config=config, snapshot_hash=HASH_A, chunk_hash=HASH_B,
    )
    retriever = VectorRetriever(snapshot, sidecar, _embedder(config=config.embedding), snapshot_hash=HASH_A, chunk_hash=HASH_B)
    hit = retriever.search("question", top_k=1)[0]
    assert hit.evidence.source_ids == ("passage",)
    metadata = hit.evidence.metadata
    assert metadata["relation_source_ids"] == ("relation", "claim")
    assert metadata["claim_status"] == "proposed"
    assert metadata["claim_path"] == "/claims/teaching.md"
    assert metadata["claim_id"] == "claim-1"
    report = assemble_context((hit,), snapshot, strict=True)
    packet = report.packets[0]
    assert packet.relation_source_ids == ("relation", "claim")
    assert packet.claim_status == "proposed"
    assert packet.claim_path == "/claims/teaching.md"
    sidecar.close()
