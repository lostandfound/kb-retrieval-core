from __future__ import annotations

import pytest

from kb_retrieval_core import (
    Claim,
    Entity,
    LexicalIndex,
    Snapshot,
    build_lexical_index,
    normalize_text,
)


def _snapshot() -> Snapshot:
    title_match = Entity(
        "/people/title.md",
        "Person",
        "宮城長順",
        aliases=("Miyagi Chojun",),
        source_ids=("ref:title",),
        content="沖縄の歴史を研究した人物。",
        claims=(
            Claim(
                claim_id="claim:title",
                statement="宮城長順は人物である",
                status="proposed",
                confidence="A",
                source_ids=("ref:title",),
            ),
        ),
    )
    alias_match = Entity(
        "/people/alias.md",
        "Person",
        "別の人物",
        aliases=("先生",),
        source_ids=("ref:alias",),
        content="別の人物について。",
    )
    body_match = Entity(
        "/people/body.md",
        "Person",
        "別人",
        source_ids=("ref:body",),
        content="宮城長順について本文にだけ記載する。",
    )
    sectioned = Entity(
        "/styles/goju.md",
        "Style",
        "剛柔流",
        source_ids=("ref:goju",),
        content="# 歴史\n成立の説明。\n# 特徴\n呼吸を重視する。\n",
    )
    return Snapshot(entities=(title_match, alias_match, body_match, sectioned))


def test_japanese_queries_without_spaces_rank_title_and_alias_above_body() -> None:
    index = LexicalIndex(_snapshot())

    hits = index.search_entities("宮城長順", top_k=3)

    assert [hit.evidence.entity_path for hit in hits] == [
        "/people/title.md",
        "/people/body.md",
    ]
    assert hits[0].evidence.source_ids == ("title",)
    assert hits[0].evidence.metadata["result_type"] == "entity"
    claim_metadata = hits[0].evidence.metadata["claims"][0]
    assert claim_metadata["claim_id"] == "claim:title"
    assert claim_metadata["status"] == "proposed"
    assert claim_metadata["confidence"] == "A"
    assert claim_metadata["source_ids"] == ("ref:title",)

    alias_hits = index.search_entities("先生")
    assert alias_hits[0].evidence.entity_path == "/people/alias.md"


def test_chunk_search_preserves_section_and_chunk_provenance() -> None:
    index = build_lexical_index(_snapshot())

    hits = index.search_chunks("呼吸", top_k=2)

    assert hits[0].evidence.entity_path == "/styles/goju.md"
    assert hits[0].evidence.section == "特徴"
    assert hits[0].evidence.source_ids == ("goju",)
    assert hits[0].evidence.metadata["chunk_id"] == "/styles/goju.md#特徴"
    assert hits[0].evidence.metadata["content_hash"] == next(
        chunk.content_hash
        for chunk in index.snapshot.chunks
        if chunk.chunk_id == "/styles/goju.md#特徴"
    )
    assert hits[0].retriever == "lexical.chunk"


def test_lexical_results_are_deterministic_with_stable_ties() -> None:
    snapshot = Snapshot(
        entities=(
            Entity("/b.md", "Term", "同じ", content=""),
            Entity("/a.md", "Term", "同じ", content=""),
        )
    )
    first = LexicalIndex(snapshot).search_entities("同じ", top_k=2)
    second = LexicalIndex(snapshot).search_entities("同じ", top_k=2)

    assert first == second
    assert [hit.rank for hit in first] == [1, 2]
    assert [hit.evidence.entity_path for hit in first] == ["/a.md", "/b.md"]


def test_top_k_and_empty_queries_are_explicit() -> None:
    index = LexicalIndex(_snapshot())
    for top_k in (0, -1, True):
        with pytest.raises(ValueError, match="top_k"):
            index.search_entities("宮城", top_k=top_k)
    for query in ("", " \t\n"):
        with pytest.raises(ValueError, match="normalization"):
            index.search_chunks(query)
    with pytest.raises(TypeError, match="query"):
        index.search_entities(None)  # type: ignore[arg-type]


def test_normalization_is_unicode_deterministic_and_short_queries_work() -> None:
    assert normalize_text(" ＡＢＣ　\n") == "abc"
    index = LexicalIndex(_snapshot())

    assert index.search_entities("剛", top_k=1)[0].evidence.entity_path == "/styles/goju.md"
    assert index.search_entities("別", top_k=1)[0].evidence.entity_path == "/people/alias.md"
