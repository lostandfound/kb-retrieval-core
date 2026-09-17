"""Versioned, disposable SQLite sidecar for embedding vectors."""

from __future__ import annotations

import json
import os
import sqlite3
import struct
import tempfile
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Mapping, Sequence

from ._normalization import normalize_json
from .embeddings import EmbeddingConfig, VectorIndexConfig, vector_index_config_to_dict


VECTOR_SIDECAR_FORMAT = 1
_SCHEMA_VERSION = 1


class VectorSidecarError(ValueError):
    """A vector sidecar is missing, corrupt, or incompatible."""


@dataclass(frozen=True, slots=True)
class VectorRecord:
    chunk_id: str
    content_hash: str
    vector: tuple[float, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.chunk_id, str) or not self.chunk_id.strip():
            raise ValueError("chunk_id must be a non-empty string")
        _validate_digest(self.content_hash, "content_hash")
        if isinstance(self.vector, (str, bytes)):
            raise TypeError("vector must be a sequence of numbers")
        values: list[float] = []
        for item in self.vector:
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                raise TypeError("vector must contain only numbers")
            value = float(item)
            if not isfinite(value):
                raise ValueError("vector must contain only finite numbers")
            values.append(value)
        object.__setattr__(self, "vector", tuple(values))


class SQLiteVectorSidecar:
    """Open vector artifact tied to one lexical snapshot and chunk set."""

    def __init__(
        self,
        database_path: Path,
        connection: sqlite3.Connection,
        manifest: Mapping[str, object],
    ) -> None:
        self.database_path = database_path
        self._connection = connection
        self.manifest = dict(manifest)
        self._records: tuple[VectorRecord, ...] | None = None

    @classmethod
    def build(
        cls,
        records: Sequence[VectorRecord],
        path: str | Path,
        *,
        config: VectorIndexConfig,
        snapshot_hash: str,
        chunk_hash: str,
    ) -> "SQLiteVectorSidecar":
        if not isinstance(config, VectorIndexConfig):
            raise TypeError("config must be a VectorIndexConfig")
        _validate_digest(snapshot_hash, "snapshot_hash")
        _validate_digest(chunk_hash, "chunk_hash")
        prepared = _validate_records(records, config.embedding.dimension)
        database_path, manifest_path = _paths(path)
        database_path.parent.mkdir(parents=True, exist_ok=True)
        manifest = _manifest(prepared, config, snapshot_hash, chunk_hash)
        temporary_database = _temporary_file(database_path, ".tmp")
        temporary_manifest = _temporary_file(manifest_path, ".tmp")
        connection = sqlite3.connect(str(temporary_database))
        try:
            _create_schema(connection)
            _write_records(connection, prepared, manifest)
            connection.commit()
            _write_manifest(temporary_manifest, manifest)
        except Exception:
            connection.rollback()
            connection.close()
            temporary_database.unlink(missing_ok=True)
            temporary_manifest.unlink(missing_ok=True)
            raise
        connection.close()
        try:
            _replace_pair(
                temporary_database,
                database_path,
                temporary_manifest,
                manifest_path,
            )
            connection = sqlite3.connect(str(database_path))
            _check_schema(connection)
        except Exception:
            temporary_database.unlink(missing_ok=True)
            temporary_manifest.unlink(missing_ok=True)
            raise
        return cls(database_path, connection, manifest)

    @classmethod
    def open(cls, path: str | Path) -> "SQLiteVectorSidecar":
        database_path, manifest_path = _paths(path, require_existing=True)
        try:
            connection = sqlite3.connect(str(database_path))
        except (OSError, sqlite3.Error) as exc:
            raise VectorSidecarError(f"cannot open vector sidecar {database_path}: {exc}") from exc
        try:
            _check_schema(connection)
            row = connection.execute("SELECT value FROM meta WHERE key = 'manifest'").fetchone()
            embedded = _load_mapping(row[0], "stored manifest") if row else {}
            sidecar = _read_manifest(manifest_path)
            if sidecar and embedded and sidecar != embedded:
                raise VectorSidecarError(
                    "vector manifest does not match the manifest stored in SQLite"
                )
            manifest = sidecar or embedded
            if not manifest:
                raise VectorSidecarError("vector sidecar manifest is missing")
            _validate_manifest(manifest)
            _validate_database(connection, manifest)
        except sqlite3.Error as exc:
            connection.close()
            raise VectorSidecarError(f"invalid vector sidecar {database_path}: {exc}") from exc
        except Exception:
            connection.close()
            raise
        return cls(database_path, connection, manifest)

    @property
    def records(self) -> tuple[VectorRecord, ...]:
        if self._records is None:
            dimension = int(self.manifest["dimension"])
            self._records = tuple(
                VectorRecord(row[0], row[1], _decode_vector(row[2], dimension))
                for row in self._connection.execute(
                    "SELECT chunk_id, content_hash, vector FROM vectors ORDER BY chunk_id"
                )
            )
        return self._records

    def matches(
        self,
        *,
        config: VectorIndexConfig,
        snapshot_hash: str,
        chunk_hash: str,
    ) -> bool:
        if not isinstance(config, VectorIndexConfig):
            raise TypeError("config must be a VectorIndexConfig")
        _validate_digest(snapshot_hash, "snapshot_hash")
        _validate_digest(chunk_hash, "chunk_hash")
        return (
            self.manifest.get("snapshot_hash") == snapshot_hash
            and self.manifest.get("chunk_hash") == chunk_hash
            and self.manifest.get("embedding_fingerprint") == config.embedding.fingerprint
            and self.manifest.get("vector_index_fingerprint") == config.fingerprint
            and self.manifest.get("dimension") == config.embedding.dimension
            and self.manifest.get("similarity_metric") == config.similarity_metric
            and self.manifest.get("vector_format_version") == config.format_version
        )

    def needs_rebuild(
        self,
        *,
        config: VectorIndexConfig,
        snapshot_hash: str,
        chunk_hash: str,
    ) -> bool:
        return not self.matches(
            config=config,
            snapshot_hash=snapshot_hash,
            chunk_hash=chunk_hash,
        )

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "SQLiteVectorSidecar":
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()


def canonical_vector_bytes(records: Sequence[VectorRecord]) -> bytes:
    prepared = tuple(sorted(records, key=lambda item: item.chunk_id))
    if len({item.chunk_id for item in prepared}) != len(prepared):
        raise ValueError("vector record chunk IDs must be unique")
    return b"".join(
        json.dumps(
            {
                "chunk_id": item.chunk_id,
                "content_hash": item.content_hash,
                "vector_hex": _encode_vector(item.vector).hex(),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
        for item in prepared
    )


def _validate_records(
    records: Sequence[VectorRecord], dimension: int
) -> tuple[VectorRecord, ...]:
    prepared = tuple(records)
    if any(not isinstance(item, VectorRecord) for item in prepared):
        raise TypeError("records must contain only VectorRecord values")
    if len({item.chunk_id for item in prepared}) != len(prepared):
        raise ValueError("vector record chunk IDs must be unique")
    for item in prepared:
        if len(item.vector) != dimension:
            raise ValueError(
                f"vector for {item.chunk_id!r} has dimension {len(item.vector)}; expected {dimension}"
            )
    return tuple(sorted(prepared, key=lambda item: item.chunk_id))


def _manifest(
    records: Sequence[VectorRecord],
    config: VectorIndexConfig,
    snapshot_hash: str,
    chunk_hash: str,
) -> dict[str, object]:
    return {
        "format": "kb-retrieval-vector-sqlite",
        "format_version": VECTOR_SIDECAR_FORMAT,
        "schema_version": _SCHEMA_VERSION,
        "vector_format_version": config.format_version,
        "snapshot_hash": snapshot_hash,
        "chunk_hash": chunk_hash,
        "embedding_config": config.embedding.to_dict(),
        "embedding_fingerprint": config.embedding.fingerprint,
        "vector_index_config": vector_index_config_to_dict(config),
        "vector_index_fingerprint": config.fingerprint,
        "similarity_metric": config.similarity_metric,
        "dimension": config.embedding.dimension,
        "counts": {"vectors": len(records)},
    }


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE vectors (
          chunk_id TEXT PRIMARY KEY,
          content_hash TEXT NOT NULL,
          vector BLOB NOT NULL
        );
        """
    )
    connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")


def _write_records(
    connection: sqlite3.Connection,
    records: Sequence[VectorRecord],
    manifest: Mapping[str, object],
) -> None:
    connection.execute(
        "INSERT INTO meta(key, value) VALUES ('manifest', ?)", (_json(manifest),)
    )
    connection.executemany(
        "INSERT INTO vectors(chunk_id, content_hash, vector) VALUES (?, ?, ?)",
        ((item.chunk_id, item.content_hash, _encode_vector(item.vector)) for item in records),
    )


def _check_schema(connection: sqlite3.Connection) -> None:
    present = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    missing = {"meta", "vectors"} - present
    if missing:
        raise VectorSidecarError(
            f"vector sidecar is missing tables: {', '.join(sorted(missing))}"
        )
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version != _SCHEMA_VERSION:
        raise VectorSidecarError(
            f"unsupported vector schema version {version}; expected {_SCHEMA_VERSION}"
        )


def _validate_manifest(manifest: Mapping[str, object]) -> None:
    if manifest.get("format") != "kb-retrieval-vector-sqlite":
        raise VectorSidecarError("unsupported vector sidecar format")
    for name, expected in (
        ("format_version", VECTOR_SIDECAR_FORMAT),
        ("schema_version", _SCHEMA_VERSION),
    ):
        if manifest.get(name) != expected:
            raise VectorSidecarError(
                f"unsupported vector {name} {manifest.get(name)!r}; expected {expected}"
            )
    for name in (
        "snapshot_hash",
        "chunk_hash",
        "embedding_fingerprint",
        "vector_index_fingerprint",
    ):
        _validate_digest(manifest.get(name), name)
    dimension = manifest.get("dimension")
    if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension < 1:
        raise VectorSidecarError("vector manifest dimension must be a positive integer")
    metric = manifest.get("similarity_metric")
    if not isinstance(metric, str) or not metric:
        raise VectorSidecarError("vector manifest similarity_metric must be non-empty")
    version = manifest.get("vector_format_version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise VectorSidecarError("vector_format_version must be a positive integer")
    counts = manifest.get("counts")
    if not isinstance(counts, Mapping):
        raise VectorSidecarError("vector manifest counts must be a mapping")
    count = counts.get("vectors")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise VectorSidecarError("vector count must be a non-negative integer")
    embedding_data = manifest.get("embedding_config")
    if not isinstance(embedding_data, Mapping):
        raise VectorSidecarError("embedding_config must be a mapping")
    try:
        embedding = EmbeddingConfig(**embedding_data)  # type: ignore[arg-type]
        config = VectorIndexConfig(embedding, metric, version)
    except (TypeError, ValueError) as exc:
        raise VectorSidecarError(f"invalid vector compatibility configuration: {exc}") from exc
    if embedding.dimension != dimension:
        raise VectorSidecarError("embedding_config dimension does not match manifest dimension")
    if embedding.fingerprint != manifest.get("embedding_fingerprint"):
        raise VectorSidecarError("embedding_config does not match embedding_fingerprint")
    if config.fingerprint != manifest.get("vector_index_fingerprint"):
        raise VectorSidecarError("vector configuration does not match vector_index_fingerprint")
    stored_vector_config = manifest.get("vector_index_config")
    if stored_vector_config != vector_index_config_to_dict(config):
        raise VectorSidecarError("vector_index_config does not match vector manifest fields")


def _validate_database(connection: sqlite3.Connection, manifest: Mapping[str, object]) -> None:
    expected = manifest["counts"]["vectors"]  # type: ignore[index]
    actual = connection.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
    if actual != expected:
        raise VectorSidecarError(
            f"SQLite vector count {actual} does not match manifest count {expected}"
        )
    dimension = int(manifest["dimension"])
    for chunk_id, content_hash, blob in connection.execute(
        "SELECT chunk_id, content_hash, vector FROM vectors"
    ):
        if not isinstance(chunk_id, str) or not chunk_id.strip():
            raise VectorSidecarError("stored chunk_id must be non-empty")
        _validate_digest(content_hash, f"content hash for {chunk_id!r}")
        _decode_vector(blob, dimension)


def _paths(value: str | Path, *, require_existing: bool = False) -> tuple[Path, Path]:
    path = Path(value)
    if path.suffix.lower() in {".sqlite", ".db"}:
        database = path
        manifest = path.with_name(f"{path.name}.manifest.json")
    else:
        database = path / "vector.sqlite"
        manifest = path / "vector-manifest.json"
    if require_existing and not database.is_file():
        raise VectorSidecarError(f"vector sidecar does not exist: {database}")
    return database, manifest


def _replace_pair(
    temporary_database: Path,
    database: Path,
    temporary_manifest: Path,
    manifest: Path,
) -> None:
    database_backup = _unused_path(database, ".backup")
    manifest_backup = _unused_path(manifest, ".backup")
    database_present = database.is_file()
    manifest_present = manifest.is_file()
    try:
        if database_present:
            os.replace(database, database_backup)
        if manifest_present:
            os.replace(manifest, manifest_backup)
        os.replace(temporary_database, database)
        os.replace(temporary_manifest, manifest)
    except Exception:
        database.unlink(missing_ok=True)
        manifest.unlink(missing_ok=True)
        if database_present and database_backup.is_file():
            os.replace(database_backup, database)
        if manifest_present and manifest_backup.is_file():
            os.replace(manifest_backup, manifest)
        raise
    else:
        database_backup.unlink(missing_ok=True)
        manifest_backup.unlink(missing_ok=True)


def _temporary_file(target: Path, suffix: str) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=suffix, dir=target.parent
    )
    os.close(descriptor)
    return Path(name)


def _unused_path(target: Path, suffix: str) -> Path:
    path = _temporary_file(target, suffix)
    path.unlink()
    return path


def _write_manifest(path: Path, manifest: Mapping[str, object]) -> None:
    path.write_text(_json(manifest) + "\n", encoding="utf-8")


def _read_manifest(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VectorSidecarError(f"invalid vector manifest {path}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise VectorSidecarError(f"vector manifest {path} must be a mapping")
    return dict(value)


def _load_mapping(value: str, name: str) -> dict[str, object]:
    try:
        result = json.loads(value)
    except json.JSONDecodeError as exc:
        raise VectorSidecarError(f"invalid {name}: {exc}") from exc
    if not isinstance(result, Mapping):
        raise VectorSidecarError(f"{name} must be a mapping")
    return dict(result)


def _json(value: object) -> str:
    return json.dumps(
        normalize_json(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _encode_vector(vector: Sequence[float]) -> bytes:
    return struct.pack(f">{len(vector)}d", *vector)


def _decode_vector(value: object, dimension: int) -> tuple[float, ...]:
    if not isinstance(value, bytes):
        raise VectorSidecarError("stored vector must be a BLOB")
    if len(value) != dimension * 8:
        raise VectorSidecarError(
            f"stored vector has {len(value)} bytes; expected {dimension * 8}"
        )
    vector = struct.unpack(f">{dimension}d", value)
    if any(not isfinite(item) for item in vector):
        raise VectorSidecarError("stored vector contains non-finite values")
    return vector


def _validate_digest(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise VectorSidecarError(f"{name} must be a lowercase SHA-256 digest")
    return value


__all__ = [
    "VECTOR_SIDECAR_FORMAT",
    "SQLiteVectorSidecar",
    "VectorRecord",
    "VectorSidecarError",
    "canonical_vector_bytes",
]
