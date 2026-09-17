from __future__ import annotations

import hashlib
from types import MappingProxyType

import pytest

from kb_retrieval_core import (
    Chunk,
    Entity,
    Relation,
    Snapshot,
    canonical_chunk_bytes,
    chunk_entity,
    chunk_snapshot,
    chunks_for_snapshot,
)


def test_chunk_entity_parses_japanese_preamble_duplicate_and_empty_sections() -> None:
    entity = Entity(
        entity_path="/people/example.md",
        entity_type="Person",
        title="例",
        tags=("tag",),
        source_ids=("ref:example",),
        content=(
            "前書きです。\n\n"
            "# 経歴\n"
            "最初の本文。\n\n"
            "## 空の節\n\n"
            "# 経歴\n"
            "二つ目の本文。\n"
        ),
    )

    chunks = chunk_entity(entity)

    assert [chunk.chunk_id for chunk in chunks] == [
        "/people/example.md#preamble",
        "/people/example.md#経歴",
        "/people/example.md#経歴-2",
    ]
    assert [chunk.heading for chunk in chunks] == [None, "経歴", "経歴"]
    assert chunks[1].text == "最初の本文。"
    assert chunks[1].content_hash == hashlib.sha256(chunks[1].text.encode()).hexdigest()
    assert chunks[1].source_ids == ("ref:example",)
    assert chunks[1].tags == ("tag",)


def test_chunk_entity_ignores_atx_headings_inside_fenced_code() -> None:
    entity = Entity(
        "/code.md",
        "Example",
        "Code",
        content=(
            "# Main\n"
            "before\n"
            "```python\n"
            "# not a heading\n"
            "```\n"
            "after\n"
            "## Child\n"
            "child body\n"
        ),
    )

    chunks = chunk_entity(entity)

    assert [chunk.heading for chunk in chunks] == ["Main", "Child"]
    assert "# not a heading" in chunks[0].text
    assert chunks[0].text.endswith("after")


def test_chunk_entity_repeats_are_deterministic_and_relations_are_inherited() -> None:
    relation = Relation("related_to", "literal-target", source_ids=("ref:x",))
    entity = Entity(
        "/x.md",
        "Term",
        "X",
        relations=(relation,),
        content="# A\ntext\n# A\nmore\n",
    )

    first = chunk_entity(entity)
    second = chunk_entity(entity)

    assert first == second
    assert first[0].relations == (relation,)
    assert first[1].chunk_id == "/x.md#A-2"


def test_chunk_ids_resolve_preamble_and_literal_heading_collision() -> None:
    entity = Entity(
        "/preamble.md",
        "Term",
        "Preamble",
        content="preamble text\n# preamble\nheading text\n",
    )

    chunks = chunk_entity(entity)

    assert [chunk.chunk_id for chunk in chunks] == [
        "/preamble.md#preamble",
        "/preamble.md#preamble-2",
    ]


def test_chunk_ids_resolve_duplicate_and_suffix_like_heading_collision() -> None:
    entity = Entity(
        "/suffix.md",
        "Term",
        "Suffix",
        content="# A\nfirst\n# A\nsecond\n# A-2\nliteral\n",
    )

    chunks = chunk_entity(entity)

    assert [chunk.chunk_id for chunk in chunks] == [
        "/suffix.md#A",
        "/suffix.md#A-2",
        "/suffix.md#A-2-2",
    ]


def test_chunk_snapshot_and_chunks_for_snapshot_preserve_snapshot_provenance() -> None:
    entity = Entity(
        "/x.md",
        "Term",
        "X",
        source_ids=("ref:x",),
        content="# Intro\n本文\n",
    )
    snapshot = Snapshot(entities=(entity,))

    chunked = chunk_snapshot(snapshot)

    assert chunked is not snapshot
    assert chunked.entities == snapshot.entities
    assert chunked.chunks == chunks_for_snapshot(snapshot)
    assert chunked.chunks[0].entity_path == entity.entity_path
    assert chunked.chunks[0].source_ids == entity.source_ids


def test_chunk_ordinals_preserve_source_order_and_canonical_bytes_ignore_input_order() -> None:
    headings = [("zeta", "first"), ("alpha", "second")] + [(f"h{i}", str(i)) for i in range(12)]
    entity = Entity(
        "/ordered.md",
        "Term",
        "Ordered",
        content="\n".join(f"# {heading}\n{text}" for heading, text in headings) + "\n",
    )
    chunks = chunk_entity(entity)

    assert [chunk.ordinal for chunk in chunks] == list(range(len(chunks)))
    assert [chunk.heading for chunk in chunks[:2]] == ["zeta", "alpha"]
    assert canonical_chunk_bytes(chunks) == canonical_chunk_bytes(reversed(chunks))


def test_canonical_chunks_reject_duplicate_entity_ordinals() -> None:
    entity = Entity("/duplicate.md", "Term", "Duplicate", content="# One\nbody\n# Two\nbody\n")
    chunks = chunk_entity(entity)
    duplicate = Chunk(
        chunk_id="/duplicate.md#copy",
        entity_path=chunks[1].entity_path,
        entity_type=chunks[1].entity_type,
        title=chunks[1].title,
        heading="copy",
        text="copy",
        content_hash="hash",
        ordinal=chunks[1].ordinal,
    )
    with pytest.raises(ValueError, match="duplicate chunk ordinal"):
        canonical_chunk_bytes((*chunks, duplicate))


def test_canonical_chunk_bytes_supports_immutable_mapping_metadata() -> None:
    chunk = Chunk(
        chunk_id="/item.md#section",
        entity_path="/item.md",
        entity_type="Term",
        title="Item",
        heading="Section",
        text="Body",
        content_hash="hash",
        metadata=MappingProxyType({"nested": MappingProxyType({"key": "value"})}),
    )

    assert canonical_chunk_bytes((chunk,)) == (
        b'{"chunk_id":"/item.md#section","content_hash":"hash",'
        b'"entity_path":"/item.md","entity_type":"Term",'
        b'"heading":"Section","metadata":{"nested":{"key":"value"}},'
        b'"ordinal":0,"relations":[],"source_ids":[],"tags":[],'
        b'"text":"Body","title":"Item"}\n'
    )
