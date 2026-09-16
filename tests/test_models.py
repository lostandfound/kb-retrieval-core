from __future__ import annotations

import pytest

from kb_retrieval_core import Evidence, SearchHit


def test_evidence_preserves_provenance() -> None:
    evidence = Evidence(
        entity_path="/people/miyagi-chojun.md",
        section="経歴",
        text="宮城長順は東恩納寛量に師事したとされる。",
        source_ids=("ref: miyagi-1936",),
        metadata={"type": "Person"},
    )

    assert evidence.source_ids == ("ref: miyagi-1936",)
    assert evidence.metadata["type"] == "Person"


def test_evidence_requires_bundle_root_relative_path() -> None:
    with pytest.raises(ValueError, match="bundle-root-relative"):
        Evidence(entity_path="people/example.md", text="本文")


def test_search_hit_requires_positive_rank() -> None:
    evidence = Evidence(entity_path="/terms/example.md", text="本文")

    with pytest.raises(ValueError, match="rank"):
        SearchHit(evidence=evidence, score=1.0, rank=0, retriever="lexical")

