"""Deterministic heading-aware Markdown chunking.

Only ATX headings (# through ######) outside fenced code blocks are section
boundaries. A preamble is represented with heading None and the ID suffix
#preamble. Heading IDs use the exact normalized heading text without URL
encoding: /people/example.md#経歴. Repeated heading text is disambiguated in
encounter order as #経歴-2, #経歴-3, and so on. Every emitted ID is reserved
across the whole entity; if a literal heading or preamble would collide, a
numeric suffix is appended to the candidate until it is unique (for example,
#preamble and #preamble-2). Sections containing only whitespace are omitted.
This milestone intentionally does not split long sections or add overlap.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace

from ._normalization import normalize_json, normalize_source_id
from .models import Chunk, Entity, Snapshot

_ATX_RE = re.compile(r"^[ \t]{0,3}(#{1,6})(?:[ \t]+(.*?)[ \t]*|[ \t]*)$")
_FENCE_RE = re.compile(r"^[ \t]{0,3}(\x60{3,}|~{3,})(.*)$")


def chunk_entity(entity: Entity) -> tuple[Chunk, ...]:
    """Create deterministic, heading-owned chunks for one entity."""

    sections = _sections(entity.content)
    chunks: list[Chunk] = []
    seen_headings: dict[str, int] = {}
    used_ids: set[str] = set()
    for heading, text in sections:
        if not text.strip():
            continue
        ordinal = len(chunks)
        if heading is None:
            candidate = f"{entity.entity_path}#preamble"
        else:
            count = seen_headings.get(heading, 0) + 1
            seen_headings[heading] = count
            suffix = heading if count == 1 else f"{heading}-{count}"
            candidate = f"{entity.entity_path}#{suffix}"
        base = _unique_chunk_id(candidate, used_ids)
        used_ids.add(base)
        chunks.append(
            Chunk(
                chunk_id=base,
                entity_path=entity.entity_path,
                entity_type=entity.entity_type,
                title=entity.title,
                heading=heading,
                text=text,
                content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                tags=entity.tags,
                source_ids=entity.source_ids,
                relations=entity.relations,
                ordinal=ordinal,
            )
        )
    return tuple(chunks)


def _unique_chunk_id(candidate: str, used_ids: set[str]) -> str:
    if candidate not in used_ids:
        return candidate
    suffix = 2
    while f"{candidate}-{suffix}" in used_ids:
        suffix += 1
    return f"{candidate}-{suffix}"


def chunk_snapshot(snapshot: Snapshot) -> Snapshot:
    """Return a copy of a snapshot with deterministic chunks for its entities."""

    chunks = tuple(chunk for entity in snapshot.entities for chunk in chunk_entity(entity))
    return replace(snapshot, chunks=chunks)


def chunks_for_snapshot(snapshot: Snapshot) -> tuple[Chunk, ...]:
    """Return only the chunks produced from a snapshot's entities."""

    return tuple(chunk for entity in snapshot.entities for chunk in chunk_entity(entity))


def _sections(content: str) -> tuple[tuple[str | None, str], ...]:
    lines = content.splitlines()
    sections: list[tuple[str | None, list[str]]] = []
    heading: str | None = None
    body: list[str] = []
    fence_char: str | None = None
    fence_length = 0

    for line in lines:
        fence = _FENCE_RE.match(line)
        if fence_char is not None:
            if (
                fence is not None
                and fence.group(1)[0] == fence_char
                and len(fence.group(1)) >= fence_length
                and not fence.group(2).strip()
            ):
                fence_char = None
            body.append(line)
            continue
        if fence is not None:
            marker = fence.group(1)
            fence_char = marker[0]
            fence_length = len(marker)
            body.append(line)
            continue
        match = _ATX_RE.match(line)
        if match is None:
            body.append(line)
            continue
        heading_text = _normalize_heading(match.group(2) or "")
        if not heading_text:
            body.append(line)
            continue
        sections.append((heading, body))
        heading = heading_text
        body = []
    sections.append((heading, body))

    result: list[tuple[str | None, str]] = []
    for section_heading, section_lines in sections:
        text = "\n".join(section_lines).strip()
        if text:
            result.append((section_heading, text))
    return tuple(result)


def _normalize_heading(value: str) -> str:
    value = value.strip()
    # CommonMark permits an optional closing sequence of # characters.
    return re.sub(r"[ \t]+#+[ \t]*$", "", value).strip()


def chunk_to_dict(chunk: Chunk) -> dict[str, object]:
    """Return the canonical JSON-compatible representation of a chunk."""
    return {
        "chunk_id": chunk.chunk_id,
        "entity_path": chunk.entity_path,
        "entity_type": chunk.entity_type,
        "title": chunk.title,
        "heading": chunk.heading,
        "text": chunk.text,
        "tags": list(chunk.tags),
        "source_ids": [normalize_source_id(value, allow_empty=True) for value in chunk.source_ids],
        "relations": [relation_to_dict(relation) for relation in chunk.relations],
        "content_hash": chunk.content_hash,
        "ordinal": chunk.ordinal,
        "metadata": normalize_json(chunk.metadata),
    }


def relation_to_dict(relation: Relation) -> dict[str, object]:
    return {
        "predicate": relation.predicate,
        "target": relation.target,
        "source_path": relation.source_path,
        "source_ids": [normalize_source_id(value, allow_empty=True) for value in relation.source_ids],
        "owner_source_ids": [normalize_source_id(value, allow_empty=True) for value in relation.owner_source_ids],
        "confidence": relation.confidence,
        "metadata": normalize_json(relation.metadata),
    }


def canonical_chunk_jsonl(chunks: Iterable[Chunk]) -> str:
    records = tuple(chunks)
    seen_ordinals: set[tuple[str, int]] = set()
    for chunk in records:
        key = (chunk.entity_path, chunk.ordinal)
        if key in seen_ordinals:
            raise ValueError(f"duplicate chunk ordinal for entity {chunk.entity_path!r}: {chunk.ordinal}")
        seen_ordinals.add(key)
    records = sorted(records, key=lambda chunk: (chunk.entity_path, chunk.ordinal, chunk.chunk_id))
    return "".join(
        json.dumps(chunk_to_dict(chunk), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        for chunk in records
    )


def canonical_chunk_bytes(chunks: Iterable[Chunk]) -> bytes:
    return canonical_chunk_jsonl(chunks).encode("utf-8")


def canonical_chunk_hash(chunks: Iterable[Chunk]) -> str:
    return hashlib.sha256(canonical_chunk_bytes(chunks)).hexdigest()


chunk_jsonl_bytes = canonical_chunk_bytes
chunks_hash = canonical_chunk_hash


__all__ = [
    "chunk_entity", "chunk_snapshot", "chunks_for_snapshot", "chunk_to_dict",
    "relation_to_dict", "canonical_chunk_jsonl", "canonical_chunk_bytes",
    "canonical_chunk_hash", "chunk_jsonl_bytes", "chunks_hash",
]
