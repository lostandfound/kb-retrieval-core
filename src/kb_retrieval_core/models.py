"""Stable value objects shared by retrieval backends and consumers."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True, slots=True)
class Evidence:
    """A source-addressable passage returned by a retrieval backend."""

    entity_path: str
    text: str
    section: str | None = None
    source_ids: tuple[str, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.entity_path.startswith("/"):
            raise ValueError("entity_path must be bundle-root-relative")
        if not self.text.strip():
            raise ValueError("text must not be empty")
        object.__setattr__(self, "source_ids", tuple(self.source_ids))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class SearchHit:
    """A ranked evidence item with a backend-independent score."""

    evidence: Evidence
    score: float
    rank: int
    retriever: str

    def __post_init__(self) -> None:
        if self.rank < 1:
            raise ValueError("rank must be at least 1")
        if not self.retriever.strip():
            raise ValueError("retriever must not be empty")

