from __future__ import annotations

import json
from pathlib import Path

from kb_retrieval_core import (
    GraphExpansionConfig,
    RetrievalConfig,
    assemble_context,
    build_retriever,
    load_snapshot,
)


FIXTURE = Path(__file__).parent / "fixtures" / "acceptance"


def test_consumer_contract_uses_only_public_exports_and_preserves_claim_provenance() -> None:
    snapshot = load_snapshot(
        FIXTURE / "content",
        FIXTURE / "graph.json",
        FIXTURE / "references.yml",
    )
    retriever = build_retriever(snapshot)
    hits = retriever.search(
        "Target Person",
        config=RetrievalConfig(
            mode="lexical",
            top_k=5,
            graph=GraphExpansionConfig(enabled=True, predicates=("teaches",)),
        ),
    )
    report = assemble_context(hits, snapshot, strict=True)
    payload = report.to_dict()
    json.dumps(payload, ensure_ascii=False, sort_keys=True)
    assert report.unresolved_source_ids == ()
    claim_packets = [packet for packet in report.packets if packet.claim_path]
    assert claim_packets
    assert claim_packets[0].claim_status
    assert claim_packets[0].confidence
    assert claim_packets[0].relation_source_ids
    assert claim_packets[0].requires_hedging is True
