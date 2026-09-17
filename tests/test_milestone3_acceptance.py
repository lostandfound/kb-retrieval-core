import json
from pathlib import Path

from kb_retrieval_core import (
    DeterministicTestEmbedder,
    EmbeddingConfig,
    EmbeddingDocument,
    EvaluationCase,
    GraphExpansionConfig,
    HybridRetriever,
    InjectedEmbedder,
    RetrievalConfig,
    SQLiteIndex,
    SQLiteVectorSidecar,
    VectorIndexConfig,
    VectorRecord,
    VectorRetriever,
    assemble_context,
    chunk_snapshot,
    evaluate,
    load_snapshot,
)


FIXTURE = Path(__file__).parent / "fixtures" / "acceptance"


def test_milestone3_offline_vector_hybrid_provenance_and_reopen(tmp_path: Path) -> None:
    snapshot = load_snapshot(FIXTURE / "content", FIXTURE / "graph.json", FIXTURE / "references.yml")
    with SQLiteIndex.build(snapshot, tmp_path / "lexical") as lexical:
        prepared = chunk_snapshot(snapshot)
        config = EmbeddingConfig("test", "acceptance", 2, revision="v1")
        vector_config = VectorIndexConfig(config, "dot", 1)
        records = tuple(
            VectorRecord(chunk.chunk_id, chunk.content_hash, (1.0, 0.0) if index == 0 else (0.0, 1.0))
            for index, chunk in enumerate(prepared.chunks)
        )
        sidecar_path = tmp_path / "vectors"
        sidecar = SQLiteVectorSidecar.build(
            records,
            sidecar_path,
            config=vector_config,
            snapshot_hash=str(lexical.manifest["source_hash"]),
            chunk_hash=str(lexical.manifest["chunk_hash"]),
        )
        embedder = InjectedEmbedder(config, document_embedder=lambda documents: (), query_embedder=lambda query: (1.0, 0.0))
        retriever = VectorRetriever(
            snapshot,
            sidecar,
            embedder,
            snapshot_hash=str(lexical.manifest["source_hash"]),
            chunk_hash=str(lexical.manifest["chunk_hash"]),
        )
        hit = retriever.search("offline", top_k=1)[0]
        assert hit.evidence.entity_path == prepared.chunks[0].entity_path
        assert hit.evidence.passage_source_ids
        report = assemble_context((hit,), snapshot, strict=False)
        json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True)
        reopened = SQLiteVectorSidecar.open(sidecar_path)
        assert reopened.records == sidecar.records
        reopened.close()
        sidecar.close()


def test_milestone3_full_pipeline_hybrid_graph_claim_context_and_evaluation(tmp_path: Path) -> None:
    snapshot = load_snapshot(FIXTURE / "content", FIXTURE / "graph.json", FIXTURE / "references.yml")
    with SQLiteIndex.build(snapshot, tmp_path / "lexical") as lexical:
        config = EmbeddingConfig("deterministic-test", "acceptance", 4, revision="v1")
        embedder = DeterministicTestEmbedder(config)
        embedded = embedder.embed_documents(tuple(
            EmbeddingDocument(chunk.chunk_id, chunk.text)
            for chunk in lexical.snapshot.chunks
        ))
        hashes = {chunk.chunk_id: chunk.content_hash for chunk in lexical.snapshot.chunks}
        records = tuple(VectorRecord(item.document_id, hashes[item.document_id], item.vector) for item in embedded)
        with SQLiteVectorSidecar.build(
            records,
            tmp_path / "vectors-full",
            config=VectorIndexConfig(config, "cosine", 1),
            snapshot_hash=str(lexical.manifest["source_hash"]),
            chunk_hash=str(lexical.manifest["chunk_hash"]),
        ) as sidecar:
            vector = VectorRetriever(
                lexical.snapshot, sidecar, embedder,
                snapshot_hash=str(lexical.manifest["source_hash"]),
                chunk_hash=str(lexical.manifest["chunk_hash"]),
            )
            retriever = HybridRetriever(lexical.snapshot, lexical=lexical, vector=vector)
            retrieval = RetrievalConfig(
                mode="hybrid", top_k=6,
                graph=GraphExpansionConfig(enabled=True, predicates=("teaches",)),
            )
            hits = retriever.search("Target Person", config=retrieval)
            claim_hit = next(hit for hit in hits if hit.evidence.metadata.get("claim_path") == "/claims/teaching.md")
            packet = assemble_context((claim_hit,), lexical.snapshot, strict=True).packets[0]
            assert packet.claim_path == "/claims/teaching.md"
            assert packet.relation_source_ids == ("claim",)

            report = evaluate(
                (EvaluationCase("Target Person", ("/claims/teaching.md",), "claim", kind="claim"),),
                lambda query, top_k=6: retriever.search(query, config=retrieval),
                k=6,
                retrieval_mode="hybrid",
                snapshot_hash=str(lexical.manifest["source_hash"]),
                package_identity="kb-retrieval-core@acceptance",
                embedding_fingerprint=config.fingerprint,
                vector_index_fingerprint=sidecar.manifest["vector_index_fingerprint"],
                fusion_config={"mode": "hybrid", "graph": {"enabled": True, "predicates": ["teaches"]}},
            )
            assert report.results[0].success is True
            assert report.evaluation_case_hash

            value_hits = retriever.search(
                "Source Person",
                config=RetrievalConfig(
                    mode="hybrid", top_k=8,
                    graph=GraphExpansionConfig(enabled=True, predicates=("founded-year",)),
                ),
            )
            value_claim = next(hit for hit in value_hits if hit.evidence.metadata.get("claim_path") == "/claims/year.md")
            assert value_claim.evidence.metadata["property"] == "founded-year"
            assert value_claim.evidence.metadata["value"] == "1900"
            value_report = evaluate(
                (EvaluationCase("Source Person", ("/claims/year.md",), "value-claim"),),
                lambda query, top_k=8: value_hits,
                k=8,
            )
            assert value_report.results[0].success is True


def test_milestone3_two_independent_lexical_builds_are_rank_stable(tmp_path: Path) -> None:
    snapshot = load_snapshot(FIXTURE / "content", FIXTURE / "graph.json", FIXTURE / "references.yml")
    with SQLiteIndex.build(snapshot, tmp_path / "first") as first, SQLiteIndex.build(snapshot, tmp_path / "second") as second:
        assert first.manifest == second.manifest
        assert first.search("teaches", top_k=5) == second.search("teaches", top_k=5)
