"""Persistent, offline SQLite storage for retrieval snapshots.

The SQLite file is a disposable representation of a :class:`Snapshot`.  It is
never a second source of knowledge: callers build it from a loaded snapshot
and can delete and rebuild it at any time.  Search uses the same deterministic
``LexicalIndex`` as the in-memory baseline, which keeps results identical when
an index is reopened without requiring an external service.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Mapping

from ._normalization import normalize_json, normalize_source_id
from .chunking import canonical_chunk_hash, chunk_snapshot
from .lexical import DEFAULT_NGRAM_SIZE, LexicalIndex
from .models import Claim, Chunk, Entity, Reference, Relation, Snapshot


SQLITE_INDEX_FORMAT = 1
_SCHEMA_VERSION = 1


class SQLiteIndexError(ValueError):
    """Raised when a persistent index is missing or structurally invalid."""


class SQLiteIndex:
    """A reopened snapshot backed by a persistent SQLite database.

    ``build`` creates both ``index.sqlite`` and ``manifest.json`` when given a
    directory.  Passing a ``.sqlite`` path stores the database there and puts
    the manifest beside it.  The public search methods intentionally mirror
    :class:`~kb_retrieval_core.lexical.LexicalIndex`.
    """

    def __init__(self, database_path: Path, connection: sqlite3.Connection, manifest: Mapping[str, object]) -> None:
        self.database_path = database_path
        self._connection = connection
        self.manifest = dict(manifest)
        self.ngram_size = int(self.manifest.get("ngram_size", DEFAULT_NGRAM_SIZE))
        self._snapshot: Snapshot | None = None
        self._lexical: LexicalIndex | None = None

    @classmethod
    def build(
        cls,
        snapshot: Snapshot,
        path: str | Path,
        *,
        ngram_size: int = DEFAULT_NGRAM_SIZE,
    ) -> "SQLiteIndex":
        """Persist ``snapshot`` and return an open index.

        A snapshot without chunks is deterministically chunked before storage.
        Existing derived index tables are replaced; source Markdown is never
        modified.
        """

        if not isinstance(snapshot, Snapshot):
            raise TypeError("snapshot must be a Snapshot")
        if not isinstance(ngram_size, int) or isinstance(ngram_size, bool) or ngram_size < 1:
            raise ValueError("ngram_size must be a positive integer")
        database_path, manifest_path = _paths(path)
        database_path.parent.mkdir(parents=True, exist_ok=True)
        prepared = chunk_snapshot(snapshot) if not snapshot.chunks else snapshot
        manifest = _manifest(prepared, ngram_size)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{database_path.name}.", suffix=".tmp", dir=database_path.parent
        )
        os.close(descriptor)
        temporary_path = Path(temporary_name)
        connection = sqlite3.connect(str(temporary_path))
        try:
            _create_schema(connection)
            _write_snapshot(connection, prepared, ngram_size)
            connection.commit()
        except Exception:
            connection.rollback()
            connection.close()
            temporary_path.unlink(missing_ok=True)
            raise
        connection.close()
        try:
            os.replace(temporary_path, database_path)
            _write_manifest(manifest_path, manifest)
            connection = sqlite3.connect(str(database_path))
            _check_schema(connection)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
        return cls(database_path, connection, manifest)

    # ``create`` and ``persist`` are convenient aliases for callers that use
    # the operation-oriented terminology in the architecture document.
    create = build
    persist = build

    @classmethod
    def open(cls, path: str | Path) -> "SQLiteIndex":
        """Open an existing persistent index without source files."""

        database_path, manifest_path = _paths(path, require_existing=True)
        try:
            connection = sqlite3.connect(str(database_path))
        except (OSError, sqlite3.Error) as exc:
            raise SQLiteIndexError(f"cannot open SQLite index {database_path}: {exc}") from exc
        try:
            _check_schema(connection)
            row = connection.execute("SELECT value FROM meta WHERE key = 'manifest'").fetchone()
            stored_manifest = _loads_mapping(row[0], "stored manifest") if row is not None else {}
            sidecar_manifest = _read_manifest(manifest_path)
            if sidecar_manifest and stored_manifest and sidecar_manifest != stored_manifest:
                raise SQLiteIndexError("manifest.json does not match the manifest stored in SQLite")
            manifest = sidecar_manifest or stored_manifest
            if not manifest:
                raise SQLiteIndexError(f"missing manifest for SQLite index {database_path}")
            _validate_manifest(manifest)
            _validate_counts(connection, manifest)
        except sqlite3.Error as exc:
            connection.close()
            raise SQLiteIndexError(f"cannot open SQLite index {database_path}: {exc}") from exc
        except Exception:
            connection.close()
            raise
        return cls(database_path, connection, manifest)

    load = open

    @property
    def snapshot(self) -> Snapshot:
        if self._snapshot is None:
            self._snapshot = _read_snapshot(self._connection)
        return self._snapshot

    @property
    def lexical(self) -> LexicalIndex:
        if self._lexical is None:
            self._lexical = LexicalIndex(self.snapshot, ngram_size=self.ngram_size)
        return self._lexical

    def search_entities(self, query: str, top_k: int = 5):
        return self.lexical.search_entities(query, top_k=top_k)

    def search_chunks(self, query: str, top_k: int = 5):
        return self.lexical.search_chunks(query, top_k=top_k)

    def search(self, query: str, top_k: int = 5):
        return self.lexical.search(query, top_k=top_k)

    def matches_snapshot(self, snapshot: Snapshot) -> bool:
        """Return whether this derived index represents ``snapshot``."""

        if not isinstance(snapshot, Snapshot):
            raise TypeError("snapshot must be a Snapshot")
        prepared = chunk_snapshot(snapshot) if not snapshot.chunks else snapshot
        expected = _manifest(prepared, self.ngram_size)
        return (
            self.manifest.get("source_hash") == expected["source_hash"]
            and self.manifest.get("chunk_hash") == expected["chunk_hash"]
        )

    def needs_rebuild(self, snapshot: Snapshot) -> bool:
        return not self.matches_snapshot(snapshot)

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "SQLiteIndex":
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()


def build_sqlite_index(snapshot: Snapshot, path: str | Path, *, ngram_size: int = DEFAULT_NGRAM_SIZE) -> SQLiteIndex:
    return SQLiteIndex.build(snapshot, path, ngram_size=ngram_size)


def open_sqlite_index(path: str | Path) -> SQLiteIndex:
    return SQLiteIndex.open(path)


# Explicitly named aliases make the persistence boundary discoverable while
# retaining one implementation and one schema.
PersistentSQLiteIndex = SQLiteIndex
PersistentIndex = SQLiteIndex


def _paths(value: str | Path, *, require_existing: bool = False) -> tuple[Path, Path]:
    path = Path(value)
    if path.suffix.lower() in {".sqlite", ".db"}:
        database = path
        manifest = path.with_name("manifest.json")
    else:
        database = path / "index.sqlite"
        manifest = path / "manifest.json"
    if require_existing and (not database.is_file()):
        raise SQLiteIndexError(f"SQLite index does not exist: {database}")
    return database, manifest


def _manifest(snapshot: Snapshot, ngram_size: int) -> dict[str, object]:
    entities = [_entity_record(entity) for entity in sorted(snapshot.entities, key=lambda item: item.entity_path)]
    source_bytes = json.dumps(
        {"entities": entities, "relations": sorted((_relation_record(item) for item in snapshot.relations), key=_canonical_record_key), "claims": sorted((_claim_record(item) for item in snapshot.claims), key=_canonical_record_key), "references": sorted((_reference_record(item) for item in snapshot.references), key=_canonical_record_key)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "format": "kb-retrieval-sqlite",
        "format_version": SQLITE_INDEX_FORMAT,
        "schema_version": _SCHEMA_VERSION,
        "ngram_size": ngram_size,
        "chunk_hash": canonical_chunk_hash(snapshot.chunks),
        "source_hash": hashlib.sha256(source_bytes).hexdigest(),
        "counts": {
            "entities": len(snapshot.entities),
            "chunks": len(snapshot.chunks),
            "relations": len(snapshot.relations),
            "claims": len(snapshot.claims),
            "references": len(snapshot.references),
        },
        "source_metadata": normalize_json(snapshot.metadata),
    }


def _canonical_record_key(value: Mapping[str, object]) -> str:
    return _json(value)


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS entities (
          entity_path TEXT PRIMARY KEY, entity_type TEXT NOT NULL, title TEXT NOT NULL,
          description TEXT, tags_json TEXT NOT NULL, aliases_json TEXT NOT NULL,
          source_ids_json TEXT NOT NULL, content TEXT NOT NULL, timestamp TEXT,
          metadata_json TEXT NOT NULL, relations_json TEXT NOT NULL, claims_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS chunks (
          chunk_id TEXT PRIMARY KEY, entity_path TEXT NOT NULL, ordinal INTEGER NOT NULL,
          entity_type TEXT NOT NULL, title TEXT NOT NULL, heading TEXT, text TEXT NOT NULL,
          content_hash TEXT NOT NULL, tags_json TEXT NOT NULL, source_ids_json TEXT NOT NULL,
          relations_json TEXT NOT NULL, metadata_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS relations (
          relation_id INTEGER PRIMARY KEY, predicate TEXT NOT NULL, target TEXT NOT NULL,
          source_ids_json TEXT NOT NULL, source_path TEXT, metadata_json TEXT NOT NULL,
          confidence TEXT, owner_source_ids_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS claims (
          claim_key TEXT PRIMARY KEY, claim_id TEXT, statement TEXT, status TEXT,
          confidence TEXT, source_ids_json TEXT NOT NULL, subject TEXT, predicate TEXT,
          target TEXT, metadata_json TEXT NOT NULL, claim_path TEXT, property TEXT, value_json TEXT
        );
        CREATE TABLE IF NOT EXISTS "references" (
          reference_id TEXT PRIMARY KEY, title TEXT NOT NULL, authors_json TEXT NOT NULL,
          year INTEGER, url TEXT, metadata_json TEXT NOT NULL, author TEXT
        );
        CREATE INDEX IF NOT EXISTS chunks_entity_path ON chunks(entity_path);
        CREATE INDEX IF NOT EXISTS relations_source_path ON relations(source_path);
        """
    )
    connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")


def _check_schema(connection: sqlite3.Connection) -> None:
    try:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    except sqlite3.Error as exc:
        raise SQLiteIndexError(f"invalid SQLite database: {exc}") from exc
    required = {"meta", "entities", "chunks", "relations", "claims", "references"}
    present = {row[0] for row in rows}
    missing = sorted(required - present)
    if missing:
        raise SQLiteIndexError(f"SQLite index is missing tables: {', '.join(missing)}")
    schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
    if schema_version != _SCHEMA_VERSION:
        raise SQLiteIndexError(
            f"unsupported SQLite schema version {schema_version}; expected {_SCHEMA_VERSION}"
        )


def _validate_manifest(manifest: Mapping[str, object]) -> None:
    if manifest.get("format") != "kb-retrieval-sqlite":
        raise SQLiteIndexError("unsupported SQLite index format")
    if manifest.get("format_version") != SQLITE_INDEX_FORMAT:
        raise SQLiteIndexError(
            f"unsupported SQLite index format version {manifest.get('format_version')!r}; "
            f"expected {SQLITE_INDEX_FORMAT}"
        )
    if manifest.get("schema_version") != _SCHEMA_VERSION:
        raise SQLiteIndexError(
            f"manifest schema version {manifest.get('schema_version')!r} does not match "
            f"SQLite schema version {_SCHEMA_VERSION}"
        )
    ngram_size = manifest.get("ngram_size")
    if not isinstance(ngram_size, int) or isinstance(ngram_size, bool) or ngram_size < 1:
        raise SQLiteIndexError("manifest ngram_size must be a positive integer")
    for name in ("source_hash", "chunk_hash"):
        value = manifest.get(name)
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise SQLiteIndexError(f"manifest {name} must be a SHA-256 hex digest")


def _validate_counts(connection: sqlite3.Connection, manifest: Mapping[str, object]) -> None:
    counts = manifest.get("counts")
    if not isinstance(counts, Mapping):
        raise SQLiteIndexError("manifest counts must be a mapping")
    for name in ("entities", "chunks", "relations", "claims", "references"):
        expected = counts.get(name)
        if not isinstance(expected, int) or isinstance(expected, bool) or expected < 0:
            raise SQLiteIndexError(f"manifest count for {name} must be a non-negative integer")
        table = '"references"' if name == "references" else name
        actual = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if actual != expected:
            raise SQLiteIndexError(
                f"SQLite {name} count {actual} does not match manifest count {expected}"
            )


def _write_snapshot(connection: sqlite3.Connection, snapshot: Snapshot, ngram_size: int) -> None:
    for table in ("meta", "entities", "chunks", "relations", "claims", '"references"'):
        connection.execute(f"DELETE FROM {table}")
    manifest = _manifest(snapshot, ngram_size)
    # The caller writes the authoritative ngram_size manifest; this copy in
    # SQLite is only a fallback for indexes moved without their sidecar.
    connection.execute("INSERT INTO meta(key, value) VALUES ('manifest', ?)", (_json(manifest),))
    connection.execute("INSERT INTO meta(key, value) VALUES ('snapshot_metadata', ?)", (_json(snapshot.metadata),))
    for entity in snapshot.entities:
        record = _entity_record(entity)
        connection.execute(
            "INSERT INTO entities VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (record["entity_path"], record["entity_type"], record["title"], record["description"], _json(record["tags"]), _json(record["aliases"]), _json(record["source_ids"]), record["content"], record["timestamp"], _json(record["metadata"]), _json(record["relations"]), _json(record["claims"])),
        )
    for chunk in snapshot.chunks:
        record = _chunk_record(chunk)
        connection.execute(
            "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (record["chunk_id"], record["entity_path"], record["ordinal"], record["entity_type"], record["title"], record["heading"], record["text"], record["content_hash"], _json(record["tags"]), _json(record["source_ids"]), _json(record["relations"]), _json(record["metadata"])),
        )
    for relation_id, relation in enumerate(snapshot.relations):
        record = _relation_record(relation)
        connection.execute(
            "INSERT INTO relations VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (relation_id, record["predicate"], record["target"], _json(record["source_ids"]), record["source_path"], _json(record["metadata"]), record["confidence"], _json(record["owner_source_ids"])),
        )
    for claim in snapshot.claims:
        record = _claim_record(claim)
        key = claim.claim_path or claim.claim_id or hashlib.sha256(_json(record).encode()).hexdigest()
        connection.execute(
            "INSERT INTO claims VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (key, record["claim_id"], record["statement"], record["status"], record["confidence"], _json(record["source_ids"]), record["subject"], record["predicate"], record["target"], _json(record["metadata"]), record["claim_path"], record["property"], None if record["value"] is None else _json(record["value"])),
        )
    for reference in snapshot.references:
        record = _reference_record(reference)
        connection.execute(
            "INSERT INTO \"references\" VALUES (?, ?, ?, ?, ?, ?, ?)",
            (record["reference_id"], record["title"], _json(record["authors"]), record["year"], record["url"], _json(record["metadata"]), record["author"]),
        )


def _read_snapshot(connection: sqlite3.Connection) -> Snapshot:
    entities_by_path: dict[str, Entity] = {}
    for row in connection.execute("SELECT * FROM entities ORDER BY entity_path"):
        entities_by_path[row[0]] = _entity_from({"entity_path": row[0], "entity_type": row[1], "title": row[2], "description": row[3], "tags": _loads(row[4]), "aliases": _loads(row[5]), "source_ids": _loads(row[6]), "content": row[7], "timestamp": row[8], "metadata": _loads(row[9]), "relations": _loads(row[10]), "claims": _loads(row[11])})
    chunks = tuple(_chunk_from({"chunk_id": row[0], "entity_path": row[1], "ordinal": row[2], "entity_type": row[3], "title": row[4], "heading": row[5], "text": row[6], "content_hash": row[7], "tags": _loads(row[8]), "source_ids": _loads(row[9]), "relations": _loads(row[10]), "metadata": _loads(row[11])}) for row in connection.execute("SELECT * FROM chunks ORDER BY entity_path, ordinal, chunk_id"))
    relations = tuple(_relation_from({"predicate": row[1], "target": row[2], "source_ids": _loads(row[3]), "source_path": row[4], "metadata": _loads(row[5]), "confidence": row[6], "owner_source_ids": _loads(row[7])}) for row in connection.execute("SELECT * FROM relations ORDER BY relation_id"))
    claims = tuple(_claim_from({"claim_id": row[1], "statement": row[2], "status": row[3], "confidence": row[4], "source_ids": _loads(row[5]), "subject": row[6], "predicate": row[7], "target": row[8], "metadata": _loads(row[9]), "claim_path": row[10], "property": row[11], "value": None if row[12] is None else _loads(row[12])}) for row in connection.execute("SELECT * FROM claims ORDER BY claim_key"))
    references = tuple(Reference(row[0], row[1], tuple(_loads(row[2])), row[3], row[4], _loads(row[5]), author=row[6]) for row in connection.execute("SELECT * FROM \"references\" ORDER BY reference_id"))
    metadata_row = connection.execute("SELECT value FROM meta WHERE key = 'snapshot_metadata'").fetchone()
    metadata = {} if metadata_row is None else _loads_mapping(metadata_row[0], "snapshot metadata")
    return Snapshot(tuple(entities_by_path.values()), chunks, relations, claims, references, metadata)


def _write_manifest(path: Path, manifest: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(_json(manifest) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _read_manifest(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SQLiteIndexError(f"invalid manifest {path}: {exc}") from exc
    return dict(value) if isinstance(value, Mapping) else {}


def _json(value: object) -> str:
    return json.dumps(normalize_json(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads(value: str) -> Any:
    return json.loads(value)


def _loads_mapping(value: str, label: str) -> dict[str, object]:
    result = _loads(value)
    if not isinstance(result, Mapping):
        raise SQLiteIndexError(f"{label} must be a mapping")
    return dict(result)


def _relation_record(value: Relation) -> dict[str, object]:
    return {"predicate": value.predicate, "target": value.target, "source_ids": [normalize_source_id(item, allow_empty=True) for item in value.source_ids], "source_path": value.source_path, "metadata": normalize_json(value.metadata), "confidence": value.confidence, "owner_source_ids": [normalize_source_id(item, allow_empty=True) for item in value.owner_source_ids]}


def _relation_from(value: Mapping[str, object]) -> Relation:
    return Relation(value["predicate"], value["target"], tuple(value.get("source_ids", ())), value.get("source_path"), value.get("metadata", {}), value.get("confidence"), tuple(value.get("owner_source_ids", ())))  # type: ignore[arg-type]


def _claim_record(value: Claim) -> dict[str, object]:
    return {"claim_id": value.claim_id, "statement": value.statement, "status": value.status, "confidence": value.confidence, "source_ids": [normalize_source_id(item, allow_empty=True) for item in value.source_ids], "subject": value.subject, "predicate": value.predicate, "target": value.target, "metadata": normalize_json(value.metadata), "claim_path": value.claim_path, "property": value.property, "value": normalize_json(value.value) if value.value is not None else None}


def _claim_from(value: Mapping[str, object]) -> Claim:
    return Claim(**{key: value.get(key) for key in ("claim_id", "statement", "status", "confidence", "subject", "predicate", "target", "metadata", "claim_path", "property", "value")}, source_ids=tuple(value.get("source_ids", ())))  # type: ignore[arg-type]


def _entity_record(value: Entity) -> dict[str, object]:
    return {"entity_path": value.entity_path, "entity_type": value.entity_type, "title": value.title, "description": value.description, "tags": list(value.tags), "aliases": list(value.aliases), "source_ids": [normalize_source_id(item, allow_empty=True) for item in value.source_ids], "content": value.content, "timestamp": value.timestamp, "metadata": normalize_json(value.metadata), "relations": [_relation_record(item) for item in value.relations], "claims": [_claim_record(item) for item in value.claims]}


def _entity_from(value: Mapping[str, object]) -> Entity:
    return Entity(value["entity_path"], value["entity_type"], value["title"], tuple(value.get("tags", ())), tuple(value.get("source_ids", ())), tuple(_relation_from(item) for item in value.get("relations", ())), tuple(_claim_from(item) for item in value.get("claims", ())), tuple(value.get("aliases", ())), value.get("description"), value.get("metadata", {}), value.get("content", ""), value.get("timestamp"))  # type: ignore[arg-type]


def _chunk_record(value: Chunk) -> dict[str, object]:
    return {"chunk_id": value.chunk_id, "entity_path": value.entity_path, "entity_type": value.entity_type, "title": value.title, "heading": value.heading, "text": value.text, "content_hash": value.content_hash, "tags": list(value.tags), "source_ids": [normalize_source_id(item, allow_empty=True) for item in value.source_ids], "relations": [_relation_record(item) for item in value.relations], "metadata": normalize_json(value.metadata), "ordinal": value.ordinal}


def _chunk_from(value: Mapping[str, object]) -> Chunk:
    return Chunk(value["chunk_id"], value["entity_path"], value["entity_type"], value["title"], value.get("heading"), value["text"], value["content_hash"], tuple(value.get("tags", ())), tuple(value.get("source_ids", ())), tuple(_relation_from(item) for item in value.get("relations", ())), value.get("metadata", {}), value.get("ordinal", 0))  # type: ignore[arg-type]


def _reference_record(value: Reference) -> dict[str, object]:
    return {"reference_id": value.reference_id, "title": value.title, "authors": list(value.authors), "year": value.year, "url": value.url, "metadata": normalize_json(value.metadata), "author": value.author}


__all__ = ["SQLITE_INDEX_FORMAT", "SQLiteIndexError", "SQLiteIndex", "PersistentSQLiteIndex", "PersistentIndex", "build_sqlite_index", "open_sqlite_index"]
