"""Assemble immutable source-resolved evidence packets."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Mapping

from .models import Reference, SearchHit, Snapshot


class ContextAssemblyError(ValueError):
    """Raised when evidence provenance or metadata cannot be assembled."""


@dataclass(frozen=True, slots=True)
class ContextReference:
    reference_id: str
    title: str | None = None
    authors: tuple[str, ...] = ()
    year: int | None = None
    url: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)
    resolved: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.reference_id, str) or not self.reference_id.strip():
            raise ValueError("reference_id must be a non-empty string")
        if isinstance(self.authors, (str, bytes)) or any(not isinstance(item, str) for item in self.authors):
            raise TypeError("authors must contain only strings")
        if self.title is not None and not isinstance(self.title, str):
            raise TypeError("title must be a string or None")
        if self.url is not None and not isinstance(self.url, str):
            raise TypeError("url must be a string or None")
        if self.year is not None and (isinstance(self.year, bool) or not isinstance(self.year, int)):
            raise TypeError("year must be an integer or None")
        if not isinstance(self.resolved, bool):
            raise TypeError("resolved must be a boolean")
        object.__setattr__(self, "authors", tuple(self.authors))
        object.__setattr__(self, "metadata", _freeze(self.metadata, "reference metadata"))

    @classmethod
    def from_reference(cls, reference: Reference) -> "ContextReference":
        return cls(reference.reference_id, reference.title, reference.authors, reference.year, reference.url, reference.metadata, True)

    @classmethod
    def unresolved(cls, reference_id: str) -> "ContextReference":
        return cls(reference_id, metadata={"unresolved": True}, resolved=False)

    def to_dict(self) -> dict[str, object]:
        return {"id": self.reference_id, "title": self.title, "authors": list(self.authors), "year": self.year, "url": self.url, "metadata": _json_value(self.metadata, "reference metadata"), "resolved": self.resolved}


@dataclass(frozen=True, slots=True)
class EvidencePacket:
    rank: int
    score: float
    retriever: str
    entity_path: str
    section: str | None
    text: str
    source_ids: tuple[str, ...]
    references: tuple[ContextReference, ...]
    metadata: Mapping[str, object] = field(default_factory=dict)
    claim_status: str | None = None
    confidence: str | None = None
    claim_id: str | None = None
    affirmative: bool | None = None
    relation: Mapping[str, object] | None = None
    passage_source_ids: tuple[str, ...] = ()
    relation_source_ids: tuple[str, ...] = ()
    claim_path: str | None = None
    property: str | None = None
    value: object | None = None
    confidence_weight: float | None = None
    requires_hedging: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.rank, bool) or not isinstance(self.rank, int) or self.rank < 1:
            raise ValueError("rank must be at least 1")
        if isinstance(self.score, bool) or not isinstance(self.score, (int, float)) or not math.isfinite(float(self.score)):
            raise ValueError("score must be a finite number")
        if not isinstance(self.retriever, str) or not self.retriever.strip():
            raise ValueError("retriever must not be empty")
        if not isinstance(self.entity_path, str) or not self.entity_path.startswith("/"):
            raise ValueError("entity_path must be bundle-root-relative")
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("text must not be empty")
        if self.section is not None and not isinstance(self.section, str):
            raise TypeError("section must be a string or None")
        for name, values in (("source_ids", self.source_ids), ("passage_source_ids", self.passage_source_ids), ("relation_source_ids", self.relation_source_ids)):
            if isinstance(values, (str, bytes)) or any(not isinstance(item, str) or not item.strip() for item in values):
                raise ValueError(f"{name} must contain non-empty strings")
        if self.confidence is not None and (not isinstance(self.confidence, str) or not self.confidence.strip()):
            raise TypeError("confidence must be a non-empty string or None")
        if self.claim_status is not None and not isinstance(self.claim_status, str):
            raise TypeError("claim_status must be a string or None")
        if self.claim_id is not None and not isinstance(self.claim_id, str):
            raise TypeError("claim_id must be a string or None")
        if self.claim_path is not None and (not isinstance(self.claim_path, str) or not self.claim_path.startswith("/")):
            raise ValueError("claim_path must be bundle-root-relative")
        if self.affirmative is not None and not isinstance(self.affirmative, bool):
            raise TypeError("affirmative must be boolean or None")
        if not isinstance(self.requires_hedging, bool):
            raise TypeError("requires_hedging must be boolean")
        if self.confidence_weight is not None and (not isinstance(self.confidence_weight, (int, float)) or not math.isfinite(float(self.confidence_weight))):
            raise ValueError("confidence_weight must be finite")
        if any(not isinstance(item, ContextReference) for item in self.references):
            raise TypeError("references must contain ContextReference objects")
        object.__setattr__(self, "score", float(self.score))
        object.__setattr__(self, "source_ids", tuple(self.source_ids))
        object.__setattr__(self, "passage_source_ids", tuple(self.passage_source_ids or self.source_ids))
        object.__setattr__(self, "relation_source_ids", tuple(self.relation_source_ids))
        object.__setattr__(self, "references", tuple(self.references))
        object.__setattr__(self, "metadata", _freeze(self.metadata, "evidence metadata"))
        if self.relation is not None:
            object.__setattr__(self, "relation", _freeze(self.relation, "relation metadata"))

    def to_dict(self) -> dict[str, object]:
        return {"rank": self.rank, "score": self.score, "retriever": self.retriever, "entity_path": self.entity_path, "section": self.section, "text": self.text, "source_ids": list(self.source_ids), "passage_source_ids": list(self.passage_source_ids), "relation_source_ids": list(self.relation_source_ids), "references": [item.to_dict() for item in self.references], "claim_status": self.claim_status, "confidence": self.confidence, "confidence_weight": self.confidence_weight, "claim_id": self.claim_id, "claim_path": self.claim_path, "property": self.property, "value": _json_value(self.value, "claim value") if self.value is not None else None, "affirmative": self.affirmative, "requires_hedging": self.requires_hedging, "relation": _json_value(self.relation, "relation metadata") if self.relation is not None else None, "metadata": _json_value(self.metadata, "evidence metadata")}


@dataclass(frozen=True, slots=True)
class ContextReport:
    packets: tuple[EvidencePacket, ...]
    unresolved_source_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        packets = tuple(self.packets)
        if any(not isinstance(item, EvidencePacket) for item in packets):
            raise TypeError("packets must contain EvidencePacket objects")
        object.__setattr__(self, "packets", packets)
        object.__setattr__(self, "unresolved_source_ids", tuple(self.unresolved_source_ids))

    @property
    def evidence(self) -> tuple[EvidencePacket, ...]:
        return self.packets

    def to_dict(self) -> dict[str, object]:
        return {"packets": [packet.to_dict() for packet in self.packets], "unresolved_source_ids": list(self.unresolved_source_ids)}


def assemble_context(hits: Iterable[SearchHit], snapshot: Snapshot, *, strict: bool = True, deduplicate: bool = True) -> ContextReport:
    if not isinstance(snapshot, Snapshot):
        raise TypeError("snapshot must be a Snapshot")
    if not isinstance(strict, bool) or not isinstance(deduplicate, bool):
        raise TypeError("strict and deduplicate must be booleans")
    values = tuple(hits)
    if any(not isinstance(hit, SearchHit) for hit in values):
        raise ContextAssemblyError("hits must contain SearchHit objects")
    values = tuple(sorted(values, key=lambda hit: (hit.rank, -hit.score, hit.evidence.entity_path, hit.retriever, hit.evidence.section or "", hit.evidence.text)))
    references = {_normalize_source_id(reference.reference_id): reference for reference in snapshot.references}
    packets: list[EvidencePacket] = []
    unresolved: list[str] = []
    seen: set[tuple[object, ...]] = set()
    for index, hit in enumerate(values):
        try:
            packet = _packet_from_hit(hit, references, strict, unresolved)
        except ContextAssemblyError as exc:
            raise ContextAssemblyError(f"hit {index} ({hit.evidence.entity_path}): {exc}") from exc
        key = _packet_key(packet)
        if deduplicate and key in seen:
            continue
        seen.add(key)
        packets.append(packet)
    return ContextReport(tuple(packets), tuple(dict.fromkeys(unresolved)))


def build_context(hits: Iterable[SearchHit], snapshot: Snapshot, *, strict: bool = True, deduplicate: bool = True) -> ContextReport:
    return assemble_context(hits, snapshot, strict=strict, deduplicate=deduplicate)


assemble_evidence = assemble_context


def _packet_from_hit(hit: SearchHit, references: Mapping[str, Reference], strict: bool, unresolved: list[str]) -> EvidencePacket:
    evidence = hit.evidence
    metadata = _freeze(evidence.metadata, "evidence metadata")
    passage_ids = tuple(_normalize_source_id(item) for item in (evidence.passage_source_ids or evidence.source_ids))
    relation_ids = tuple(_normalize_source_id(item) for item in _source_values(metadata.get("relation_source_ids", metadata.get("claim_source_ids", ()))))
    all_ids = tuple(dict.fromkeys(passage_ids + relation_ids))
    resolved: list[ContextReference] = []
    for source_id in all_ids:
        reference = references.get(source_id)
        if reference is None:
            if strict:
                raise ContextAssemblyError(f"missing reference for source ID {source_id!r}")
            resolved.append(ContextReference.unresolved(source_id))
            unresolved.append(source_id)
        else:
            resolved.append(ContextReference.from_reference(reference))
    relation = metadata.get("relation")
    if relation is not None and not isinstance(relation, Mapping):
        raise ContextAssemblyError("metadata relation must be a mapping")
    if isinstance(relation, Mapping):
        for key in ("predicate", "direction"):
            if relation.get(key) is not None and (not isinstance(relation[key], str) or not relation[key].strip()):
                raise ContextAssemblyError(f"metadata relation {key} must be a non-empty string")
    claim_status = _metadata_value(metadata, "claim_status", str)
    confidence = _metadata_value(metadata, "confidence", str)
    claim_id = _metadata_value(metadata, "claim_id", str)
    return EvidencePacket(rank=hit.rank, score=hit.score, retriever=hit.retriever, entity_path=evidence.entity_path, section=evidence.section, text=evidence.text, source_ids=passage_ids, passage_source_ids=passage_ids, relation_source_ids=relation_ids, references=tuple(resolved), metadata=metadata, claim_status=claim_status, confidence=confidence, confidence_weight=_number_value(metadata, "confidence_weight"), claim_id=claim_id, claim_path=_metadata_value(metadata, "claim_path", str), property=_metadata_value(metadata, "property", str), value=metadata.get("value"), affirmative=_metadata_value(metadata, "affirmative", bool), requires_hedging=bool(metadata.get("requires_hedging", False)), relation=relation)


def _metadata_value(metadata: Mapping[str, object], key: str, expected: type) -> object:
    value = metadata.get(key)
    if value is not None and not isinstance(value, expected):
        raise ContextAssemblyError(f"metadata {key} has an invalid type")
    return value


def _number_value(metadata: Mapping[str, object], key: str) -> float | None:
    value = metadata.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ContextAssemblyError(f"metadata {key} has an invalid number")
    return float(value)


def _source_values(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, (list, tuple, set, frozenset)) or any(not isinstance(item, str) for item in value):
        raise ContextAssemblyError("source role values must contain strings")
    return tuple(value)


def _normalize_source_id(value: str) -> str:
    value = value.strip()
    return value[4:].strip() if value.casefold().startswith("ref:") else value


def _packet_key(packet: EvidencePacket) -> tuple[object, ...]:
    return (packet.entity_path, packet.section, packet.text, packet.source_ids, packet.relation_source_ids, packet.retriever, json.dumps(_json_value(packet.metadata, "evidence metadata"), ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _freeze(value: object, field_name: str) -> Mapping[str, object] | object:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ContextAssemblyError(f"{field_name} keys must be strings")
        return MappingProxyType({key: _freeze(item, field_name) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item, field_name) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item, field_name) for item in value)
    if isinstance(value, float) and not math.isfinite(value):
        raise ContextAssemblyError(f"{field_name} contains a non-finite number")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime, Path)):
        return value
    raise ContextAssemblyError(f"{field_name} contains unsupported metadata value {type(value).__name__}")


def _json_value(value: object, field_name: str) -> object:
    if isinstance(value, datetime):
        if value.tzinfo is not None and value.utcoffset() is not None and value.utcoffset().total_seconds() == 0:
            return value.isoformat().replace("+00:00", "Z")
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, Mapping):
        return {key: _json_value(item, field_name) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_json_value(item, field_name) for item in value]
    if isinstance(value, (set, frozenset)):
        items = [_json_value(item, field_name) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    if isinstance(value, float) and not math.isfinite(value):
        raise ContextAssemblyError(f"{field_name} contains a non-finite number")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ContextAssemblyError(f"{field_name} contains unsupported metadata value {type(value).__name__}")


__all__ = ["ContextAssemblyError", "ContextReference", "EvidencePacket", "ContextReport", "assemble_context", "assemble_evidence", "build_context"]
