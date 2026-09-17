import json
from pathlib import Path

from kb_retrieval_core import (
    EmbeddingConfig,
    InjectedEmbedder,
    SQLiteIndex,
    SQLiteVectorSidecar,
    VectorIndexConfig,
    VectorRecord,
    VectorRetriever,
    assemble_context,
    chunk_snapshot,
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
