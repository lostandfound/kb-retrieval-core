import math

import pytest

from kb_retrieval_core import Claim, Chunk, Entity, Evidence, Reference, Relation, SearchHit, Snapshot


def test_evidence_preserves_provenance_and_paths() -> None:
    evidence = Evidence("/people/example.md", "A passage", source_ids=("ref:book",))
    assert evidence.entity_path == "/people/example.md"
    assert evidence.source_ids == ("ref:book",)


def test_search_hit_requires_positive_rank_and_finite_score() -> None:
    evidence = Evidence("/terms/example.md", "Body")
    with pytest.raises(ValueError, match="rank"):
        SearchHit(evidence, 1.0, 0, "lexical")
    for score in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError, match="finite"):
            SearchHit(evidence, score, 1, "lexical")


def test_snapshot_models_are_frozen_and_copy_collections() -> None:
    tags = ["goju-ryu"]
    source_ids = ["ref:example"]
    relation = Relation("teacher", "/people/teacher.md", source_ids=source_ids, confidence="C")
    entity = Entity("/people/student.md", "Person", "Student", tags=tags, source_ids=source_ids, relations=[relation])
    chunk = Chunk("/people/student.md#Biography", "/people/student.md", "Person", "Student", "Biography", "A passage.", "sha256:abc", tags, source_ids, [relation])
    reference = Reference("example", "Example", author="Author", metadata={"details": {"aliases": ["one"]}})
    tags.append("history")
    source_ids.append("ref:other")
    assert entity.tags == ("goju-ryu",)
    assert chunk.source_ids == ("ref:example",)
    assert reference.authors == ("Author",)
    with pytest.raises((AttributeError, TypeError)):
        entity.title = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        reference.metadata["new"] = "value"  # type: ignore[index]


def test_claim_relation_and_value_forms_are_mutually_exclusive_and_opaque() -> None:
    relation_claim = Claim(claim_id="claim-1", claim_path="/claims/one.md", subject="/a.md", predicate="supports", target="/b.md", status="proposed", confidence="D")
    value_claim = Claim(claim_id="claim-2", claim_path="/claims/two.md", subject="/a.md", property="year", value="1900", status="accepted", confidence="A")
    assert relation_claim.target == "/b.md"
    assert value_claim.property == "year"
    with pytest.raises((TypeError, ValueError)):
        Claim(statement="A statement", confidence=0.5)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="mutually exclusive"):
        Claim(subject="/a.md", predicate="supports", target="/b.md", property="year", value="1900")


def test_snapshot_validates_graph_paths() -> None:
    entity = Entity("/a.md", "Term", "A")
    with pytest.raises(ValueError, match="unknown"):
        Snapshot(entities=(entity,), chunks=(Chunk("/missing.md#x", "/missing.md", "Term", "X", "x", "body", "hash"),))
