import json

import pytest

from kb_retrieval_core import (
    Claim,
    ContextAssemblyError,
    Entity,
    Evidence,
    GraphIndex,
    LexicalIndex,
    Reference,
    Relation,
    SearchHit,
    Snapshot,
    assemble_context,
)


def test_lexical_chunk_packet_preserves_heading_path_and_resolves_reference() -> None:
    snapshot = Snapshot(
        entities=(
            Entity(
                "/people/example.md",
                "Person",
                "Example",
                content="# History\n\nA useful passage about karate.",
                source_ids=("ref:book",),
            ),
        ),
        references=(
            Reference(
                "ref:book",
                title="A Book",
                authors=("Author",),
                year=1936,
                url="https://example.test/book",
            ),
        ),
    )
    hits = LexicalIndex(snapshot).search_chunks("useful passage")

    report = assemble_context(hits, snapshot)
    packet = report.packets[0]

    assert packet.entity_path == "/people/example.md"
    assert packet.section == "History"
    assert packet.source_ids == ("book",)
    assert packet.references[0].resolved is True
    assert packet.references[0].title == "A Book"
    assert packet.references[0].authors == ("Author",)


def test_graph_claim_packet_preserves_epistemic_and_relation_metadata() -> None:
    source = Entity(
        "/source.md",
        "Term",
        "Source",
        claims=(
            Claim(
                claim_id="claim-1",
                subject="/source.md",
                predicate="supports",
                target="/target.md",
                status="proposed",
                    confidence="C",
                source_ids=("ref:claim",),
            ),
        ),
    )
    target = Entity("/target.md", "Term", "Target", source_ids=("ref:target",))
    snapshot = Snapshot(
        entities=(source, target),
        references=(Reference("ref:claim", title="Claim source"), Reference("ref:target")),
    )
    seed = SearchHit(Evidence("/source.md", "Source"), score=1, rank=1, retriever="seed")
    hit = next(
        item
        for item in GraphIndex(snapshot).expand((seed,), expand=True)
        if item.evidence.metadata.get("claim_id") == "claim-1"
    )

    packet = assemble_context((hit,), snapshot).packets[0]

    assert packet.claim_status == "proposed"
    assert packet.confidence == "C"
    assert packet.claim_id == "claim-1"
    assert packet.affirmative is False
    assert packet.relation["predicate"] == "supports"
    assert packet.relation["direction"] == "outgoing"
    assert {reference.reference_id for reference in packet.references} == {
        "ref:claim",
        "ref:target",
    }


@pytest.mark.parametrize("confidence", ("C", "D", None))
def test_graph_relation_confidence_survives_packet_assembly_and_serialization(confidence: str | None) -> None:
    source = Entity(
        "/source.md",
        "Term",
        "Source",
        relations=(Relation("supports", "/target.md", source_ids=("ref:relation",), confidence=confidence),),
    )
    target = Entity("/target.md", "Term", "Target", source_ids=("ref:target",))
    snapshot = Snapshot(
        entities=(source, target),
        references=(Reference("ref:relation", title="Relation source"), Reference("ref:target")),
    )
    seed = SearchHit(Evidence("/source.md", "Source"), score=1, rank=1, retriever="seed")
    hit = next(
        item
        for item in GraphIndex(snapshot).expand((seed,), expand=True)
        if item.evidence.entity_path == "/target.md"
    )

    assert hit.evidence.metadata["confidence"] == confidence
    assert hit.evidence.metadata["claim_status"] is None
    packet = assemble_context((hit,), snapshot).packets[0]
    assert packet.confidence == confidence
    assert packet.claim_status is None
    assert packet.to_dict()["confidence"] == confidence


def test_reference_and_nested_metadata_are_preserved_as_json() -> None:
    reference = Reference(
        "ref:nested",
        metadata={"publisher": {"country": "JP"}, "keywords": ("a", "b")},
    )
    evidence = Evidence(
        "/item.md",
        "Text",
        source_ids=("ref:nested",),
        metadata={"nested": {"values": (1, 2)}, "flags": {"x", "y"}},
    )
    report = assemble_context(
        (SearchHit(evidence, score=1, rank=1, retriever="test"),),
        Snapshot(references=(reference,)),
    )

    value = report.to_dict()
    json.dumps(value)
    assert value["packets"][0]["references"][0]["metadata"] == {
        "keywords": ["a", "b"],
        "publisher": {"country": "JP"},
    }
    assert value["packets"][0]["metadata"]["nested"]["values"] == [1, 2]


def test_missing_reference_is_strict_by_default_and_explicit_when_non_strict() -> None:
    hit = SearchHit(
        Evidence("/item.md", "Text", source_ids=("ref:missing",)),
        score=1,
        rank=1,
        retriever="test",
    )
    snapshot = Snapshot()

    with pytest.raises(ContextAssemblyError, match="missing"):
        assemble_context((hit,), snapshot)

    report = assemble_context((hit,), snapshot, strict=False)
    reference = report.packets[0].references[0]
    assert reference.resolved is False
    assert reference.reference_id == "missing"
    assert report.unresolved_source_ids == ("missing",)


def test_ordering_and_deduplication_keep_distinct_provenance() -> None:
    snapshot = Snapshot(references=(Reference("ref:a"),))
    base = Evidence("/a.md", "same", section="One", source_ids=("ref:a",), metadata={"x": 1})
    duplicate = SearchHit(base, score=0.5, rank=2, retriever="lexical")
    higher = SearchHit(base, score=0.9, rank=1, retriever="lexical")
    other_section = SearchHit(
        Evidence("/a.md", "same", section="Two", source_ids=("ref:a",), metadata={"x": 1}),
        score=0.8,
        rank=1,
        retriever="lexical",
    )
    other_retriever = SearchHit(base, score=0.7, rank=1, retriever="graph")

    report = assemble_context((duplicate, other_retriever, other_section, higher), snapshot)

    assert len(report.packets) == 3
    assert [(packet.rank, packet.retriever, packet.section) for packet in report.packets] == [
        (1, "lexical", "One"),
        (1, "lexical", "Two"),
        (1, "graph", "One"),
    ]


@pytest.mark.parametrize(
    "metadata",
    [{"bad": object()}, {"bad": float("inf")}],
)
def test_unsupported_or_nonfinite_metadata_fails(metadata: dict[str, object]) -> None:
    hit = SearchHit(
        Evidence("/item.md", "Text", metadata=metadata),
        score=1,
        rank=1,
        retriever="test",
    )
    with pytest.raises(ContextAssemblyError):
        assemble_context((hit,), Snapshot())
