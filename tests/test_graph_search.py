from __future__ import annotations

import pytest

from kb_retrieval_core import (
    Claim,
    Entity,
    Evidence,
    GraphIndex,
    Relation,
    SearchHit,
    Snapshot,
)


def _seed(path: str = "/a.md", score: float = 1.0) -> SearchHit:
    return SearchHit(
        evidence=Evidence(entity_path=path, text=f"seed {path}", source_ids=("ref:seed",)),
        score=score,
        rank=1,
        retriever="test",
    )


def _graph_snapshot() -> Snapshot:
    a = Entity(
        "/a.md",
        "Term",
        "A",
        source_ids=("ref:a",),
        relations=(
            Relation("ordinary", "/b.md", source_ids=("ref:ordinary",)),
            Relation("chain", "/d.md", source_ids=("ref:chain",)),
        ),
        claims=(
            Claim(
                claim_id="accepted",
                subject="/a.md",
                predicate="accepted-link",
                target="/c.md",
                status="accepted",
                    confidence="A",
                source_ids=("ref:accepted",),
            ),
            Claim(
                claim_id="proposed",
                subject="/a.md",
                predicate="proposed-link",
                target="/d.md",
                status="proposed",
                    confidence="C",
                source_ids=("ref:proposed",),
            ),
            Claim(
                claim_id="rejected",
                subject="/a.md",
                predicate="rejected-link",
                target="/e.md",
                status="rejected",
                    confidence="A",
                source_ids=("ref:rejected",),
            ),
            Claim(
                claim_id="unknown",
                subject="/a.md",
                predicate="unknown-link",
                target="/f.md",
                status="future-state",
                    confidence="D",
                source_ids=("ref:unknown",),
            ),
        ),
    )
    b = Entity(
        "/b.md",
        "Term",
        "B",
        source_ids=("ref:b",),
        relations=(Relation("next", "/g.md", source_ids=("ref:next",)),),
    )
    c = Entity("/c.md", "Term", "C", source_ids=("ref:c",))
    d = Entity("/d.md", "Term", "D", source_ids=("ref:d",))
    e = Entity("/e.md", "Term", "E", source_ids=("ref:e",))
    f = Entity("/f.md", "Term", "F", source_ids=("ref:f",))
    g = Entity("/g.md", "Term", "G", source_ids=("ref:g",))
    return Snapshot(
        entities=(a, b, c, d, e, f, g),
        relations=(
            # Duplicate of A's nested ordinary relation; it must expand once.
            Relation(
                "ordinary",
                "/b.md",
                source_ids=("ref:ordinary",),
                source_path="/a.md",
            ),
            # Incoming edge to A.
            Relation(
                "incoming",
                "/a.md",
                source_ids=("ref:incoming",),
                source_path="/c.md",
            ),
        ),
    )


def test_graph_expansion_is_opt_in_and_supports_outgoing_incoming_and_filtering() -> None:
    index = GraphIndex(_graph_snapshot())
    seed = (_seed(),)

    assert index.expand(seed) == seed
    expanded = index.expand(seed, expand=True, predicates={"ordinary", "incoming"})

    assert [hit.evidence.entity_path for hit in expanded] == [
        "/a.md",
        "/b.md",
        "/c.md",
    ]
    b_hit = next(hit for hit in expanded if hit.evidence.entity_path == "/b.md")
    c_hit = next(hit for hit in expanded if hit.evidence.entity_path == "/c.md")
    assert b_hit.evidence.metadata["relation"]["direction"] == "outgoing"
    assert c_hit.evidence.metadata["relation"]["direction"] == "incoming"
    assert b_hit.evidence.metadata["source_hit"]["entity_path"] == "/a.md"
    assert b_hit.evidence.source_ids == ("b",)
    assert b_hit.evidence.metadata["relation_source_ids"] == ("ordinary",)


def test_graph_expansion_is_one_hop_and_deduplicates_nested_snapshot_edges() -> None:
    expanded = GraphIndex(_graph_snapshot()).expand((_seed(),), expand=True)

    paths = [hit.evidence.entity_path for hit in expanded]
    assert paths.count("/b.md") == 1
    assert "/g.md" not in paths


def test_claim_status_controls_eligibility_ranking_and_provenance() -> None:
    expanded = GraphIndex(_graph_snapshot()).expand((_seed(),), expand=True)
    by_path = {hit.evidence.entity_path: hit for hit in expanded}

    assert by_path["/c.md"].score > by_path["/d.md"].score
    proposed = next(
        hit
        for hit in expanded
        if hit.evidence.metadata.get("claim_id") == "proposed"
    )
    assert proposed.evidence.metadata["claim_status"] == "proposed"
    assert proposed.evidence.metadata["confidence"] == "C"
    assert proposed.evidence.metadata["claim_source_ids"] == ("proposed",)
    assert proposed.evidence.metadata["affirmative"] is False
    assert "/e.md" not in by_path
    assert "/f.md" not in by_path

    opted_in = GraphIndex(_graph_snapshot()).expand(
        (_seed(),), expand=True, include_rejected=True, include_unknown=True
    )
    opted_paths = {hit.evidence.entity_path for hit in opted_in}
    assert {"/e.md", "/f.md"} <= opted_paths
    for path in ("/e.md", "/f.md"):
        assert by_path.get(path) is None
        hit = next(item for item in opted_in if item.evidence.entity_path == path)
        assert hit.evidence.metadata["affirmative"] is False


def test_graph_expansion_ties_are_deterministic_and_options_validate() -> None:
    index = GraphIndex(_graph_snapshot(), decay=0.5, claim_decay=0.25)
    first = index.expand((_seed(),), expand=True)
    second = index.expand((_seed(),), expand=True)

    assert first == second
    assert [hit.rank for hit in first] == list(range(1, len(first) + 1))
    with pytest.raises(ValueError, match="top_k"):
        index.expand((_seed(),), expand=True, top_k=0)
    with pytest.raises(ValueError, match="decay"):
        GraphIndex(_graph_snapshot(), decay=0)
    with pytest.raises(ValueError, match="predicates"):
        index.expand((_seed(),), expand=True, predicates=[""])
    with pytest.raises(ValueError, match="unknown entity"):
        index.expand((_seed("/missing.md"),), expand=True)


def test_claim_confidence_scales_status_decay_and_none_keeps_hit() -> None:
    source = Entity(
        "/source.md",
        "Term",
        "Source",
        claims=(
            Claim(
                claim_id="high",
                subject="/source.md",
                predicate="supports",
                target="/high.md",
                status="proposed",
                    confidence="A",
                source_ids=("ref:high",),
            ),
            Claim(
                claim_id="low",
                subject="/source.md",
                predicate="supports",
                target="/low.md",
                status="proposed",
                    confidence="D",
                source_ids=("ref:low",),
            ),
            Claim(
                claim_id="missing-confidence",
                subject="/source.md",
                predicate="supports",
                target="/none.md",
                status="disputed",
                confidence=None,
                source_ids=("ref:none",),
            ),
        ),
    )
    snapshot = Snapshot(
        entities=(
            source,
            Entity("/high.md", "Term", "High", source_ids=("ref:high-target",)),
            Entity("/low.md", "Term", "Low", source_ids=("ref:low-target",)),
            Entity("/none.md", "Term", "None", source_ids=("ref:none-target",)),
        )
    )

    hits = GraphIndex(snapshot).expand((_seed("/source.md"),), expand=True)
    by_claim = {
        hit.evidence.metadata["claim_id"]: hit
        for hit in hits
        if hit.evidence.metadata.get("claim_id")
    }

    assert by_claim["high"].score > by_claim["low"].score
    assert by_claim["missing-confidence"].score > 0
    assert by_claim["missing-confidence"].evidence.metadata["confidence"] is None
    assert by_claim["missing-confidence"].evidence.metadata["claim_source_ids"] == ("none",)


def test_custom_confidence_policy_is_serialized_for_relations_and_claims() -> None:
    source = Entity(
        "/source.md",
        "Term",
        "Source",
        relations=(Relation("ordinary", "/relation-target.md", confidence="C"),),
        claims=(Claim(
            claim_id="claim",
            subject="/source.md",
            predicate="supports",
            target="/claim-target.md",
            status="accepted",
            confidence="C",
            source_ids=("claim",),
        ),),
    )
    snapshot = Snapshot(entities=(
        source,
        Entity("/relation-target.md", "Term", "Relation target"),
        Entity("/claim-target.md", "Term", "Claim target"),
    ))
    index = GraphIndex(snapshot, confidence_weights={"C": 0.9})
    hits = index.expand((_seed("/source.md"),), expand=True)
    by_path = {hit.evidence.entity_path: hit for hit in hits}
    assert by_path["/relation-target.md"].score == pytest.approx(0.75 * 0.9)
    assert by_path["/relation-target.md"].evidence.metadata["confidence_weight"] == pytest.approx(0.9)
    assert by_path["/relation-target.md"].evidence.metadata["relation"]["confidence_weight"] == pytest.approx(0.9)
    assert by_path["/claim-target.md"].evidence.metadata["confidence_weight"] == pytest.approx(0.9)
