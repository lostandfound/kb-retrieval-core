"""Small, dependency-free normalizers shared by retrieval modules.

These functions define the retrieval boundary's canonical representations.  The
module is private so the public compatibility exports continue to live in
``snapshot`` and the package root.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Mapping


def normalize_source_id(value: str, *, allow_empty: bool = False) -> str:
    """Return a trimmed, bare source ID (``ref:foo`` becomes ``foo``)."""

    if not isinstance(value, str):
        raise TypeError("source ID must be a string")
    result = value.strip()
    if result.casefold().startswith("ref:"):
        result = result[4:].strip()
    if not result and not allow_empty:
        raise ValueError("source ID must not be empty")
    return result


def normalize_json(value: object) -> object:
    """Convert supported values to deterministic JSON-compatible values."""

    if isinstance(value, datetime):
        if value.tzinfo is not None and value.utcoffset() is not None and value.utcoffset().total_seconds() == 0:
            return value.isoformat().replace("+00:00", "Z")
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("JSON mapping keys must be strings")
        return {key: normalize_json(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [normalize_json(item) for item in value]
    if isinstance(value, (set, frozenset)):
        values = [normalize_json(item) for item in value]
        return sorted(values, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("non-finite number is not JSON-compatible")
        return value
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


__all__ = ["normalize_json", "normalize_source_id"]
