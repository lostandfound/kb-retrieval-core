from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from kb_retrieval_core import (
    EmbeddingConfig,
    SQLiteIndex,
    SQLiteVectorSidecar,
    VectorIndexConfig,
    VectorRecord,
    VectorSidecarError,
    canonical_vector_bytes,
    load_snapshot,
)
import kb_retrieval_core.vector_store as vector_store_module


FIXTURE = Path(__file__).parent / "fixtures" / "acceptance"
HASH_A = "a" * 64
HASH_B = "b" * 64


def _config(**changes: object) -> VectorIndexConfig:
    embedding = EmbeddingConfig(
        "deterministic-test", "fixture", 3, revision="v1", normalization="none"
    )
    values: dict[str, object] = {
        "embedding": embedding,
        "similarity_metric": "cosine",
        "format_version": 1,
    }
    values.update(changes)
    return VectorIndexConfig(**values)  # type: ignore[arg-type]


def _records() -> tuple[VectorRecord, ...]:
    return (
        VectorRecord("/b.md#two", "2" * 64, (0.0, 1.0, 0.0)),
        VectorRecord("/a.md#one", "1" * 64, (1.0, 0.0, -1.0)),
    )


def test_vector_sidecar_round_trips_records_and_identity(tmp_path: Path) -> None:
    path = tmp_path / ".retrieval"
    config = _config()
    built = SQLiteVectorSidecar.build(
        _records(), path, config=config, snapshot_hash=HASH_A, chunk_hash=HASH_B
    )
    assert (path / "vector.sqlite").is_file()
    assert (path / "vector-manifest.json").is_file()
    assert built.records == tuple(sorted(_records(), key=lambda item: item.chunk_id))
    assert built.manifest["embedding_fingerprint"] == config.embedding.fingerprint
    assert built.manifest["vector_index_fingerprint"] == config.fingerprint
    assert built.matches(config=config, snapshot_hash=HASH_A, chunk_hash=HASH_B)
    built.close()

    reopened = SQLiteVectorSidecar.open(path)
    assert reopened.records == tuple(sorted(_records(), key=lambda item: item.chunk_id))
    assert reopened.manifest["dimension"] == 3
    assert reopened.manifest["similarity_metric"] == "cosine"
    reopened.close()


def test_vector_artifacts_are_deterministic(tmp_path: Path) -> None:
    first_path = tmp_path / "first"
    second_path = tmp_path / "second"
    first = SQLiteVectorSidecar.build(
        _records(), first_path, config=_config(), snapshot_hash=HASH_A, chunk_hash=HASH_B
    )
    second = SQLiteVectorSidecar.build(
        tuple(reversed(_records())),
        second_path,
        config=_config(),
        snapshot_hash=HASH_A,
        chunk_hash=HASH_B,
    )
    first.close()
    second.close()
    assert canonical_vector_bytes(_records()) == canonical_vector_bytes(reversed(_records()))
    assert (first_path / "vector-manifest.json").read_bytes() == (
        second_path / "vector-manifest.json"
    ).read_bytes()
    assert (first_path / "vector.sqlite").read_bytes() == (
        second_path / "vector.sqlite"
    ).read_bytes()


@pytest.mark.parametrize(
    ("config", "snapshot_hash", "chunk_hash"),
    [
        (_config(), "c" * 64, HASH_B),
        (_config(), HASH_A, "c" * 64),
        (_config(embedding=EmbeddingConfig("deterministic-test", "fixture", 4)), HASH_A, HASH_B),
        (_config(similarity_metric="dot"), HASH_A, HASH_B),
        (_config(format_version=2), HASH_A, HASH_B),
    ],
)
def test_vector_sidecar_detects_every_compatibility_mismatch(
    tmp_path: Path,
    config: VectorIndexConfig,
    snapshot_hash: str,
    chunk_hash: str,
) -> None:
    sidecar = SQLiteVectorSidecar.build(
        _records(), tmp_path / "vector", config=_config(), snapshot_hash=HASH_A, chunk_hash=HASH_B
    )
    assert sidecar.needs_rebuild(
        config=config, snapshot_hash=snapshot_hash, chunk_hash=chunk_hash
    )
    sidecar.close()


def test_vector_sidecar_rejects_invalid_records_and_corruption(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="dimension"):
        SQLiteVectorSidecar.build(
            (VectorRecord("one", "1" * 64, (1.0,)),),
            tmp_path / "wrong",
            config=_config(),
            snapshot_hash=HASH_A,
            chunk_hash=HASH_B,
        )
    with pytest.raises(ValueError, match="unique"):
        SQLiteVectorSidecar.build(
            (_records()[0], _records()[0]),
            tmp_path / "duplicate",
            config=_config(),
            snapshot_hash=HASH_A,
            chunk_hash=HASH_B,
        )

    path = tmp_path / "corrupt"
    sidecar = SQLiteVectorSidecar.build(
        _records(), path, config=_config(), snapshot_hash=HASH_A, chunk_hash=HASH_B
    )
    sidecar.close()
    (path / "vector-manifest.json").unlink()
    with sqlite3.connect(path / "vector.sqlite") as connection:
        connection.execute("DELETE FROM vectors WHERE chunk_id = ?", (_records()[0].chunk_id,))
    with pytest.raises(VectorSidecarError, match="count"):
        SQLiteVectorSidecar.open(path)


def test_vector_sidecar_rejects_manifest_configuration_fingerprint_conflict(
    tmp_path: Path,
) -> None:
    path = tmp_path / "conflict"
    sidecar = SQLiteVectorSidecar.build(
        _records(), path, config=_config(), snapshot_hash=HASH_A, chunk_hash=HASH_B
    )
    sidecar.close()
    manifest_path = path / "vector-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["embedding_fingerprint"] = "0" * 64
    serialized = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    manifest_path.write_text(serialized + "\n", encoding="utf-8")
    with sqlite3.connect(path / "vector.sqlite") as connection:
        connection.execute(
            "UPDATE meta SET value = ? WHERE key = 'manifest'", (serialized,)
        )
    with pytest.raises(VectorSidecarError, match="embedding_config"):
        SQLiteVectorSidecar.open(path)


def test_failed_build_and_manifest_commit_preserve_previous_sidecar(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "vector"
    original = SQLiteVectorSidecar.build(
        _records(), path, config=_config(), snapshot_hash=HASH_A, chunk_hash=HASH_B
    )
    original_manifest = original.manifest
    original.close()

    real_replace = vector_store_module.os.replace
    final_manifest = path / "vector-manifest.json"
    failed = False

    def fail_once(source, destination):
        nonlocal failed
        if Path(destination) == final_manifest and not failed:
            failed = True
            raise OSError("injected manifest failure")
        return real_replace(source, destination)

    monkeypatch.setattr(vector_store_module.os, "replace", fail_once)
    with pytest.raises(OSError, match="injected manifest failure"):
        SQLiteVectorSidecar.build(
            (VectorRecord("new", "3" * 64, (1.0, 1.0, 1.0)),),
            path,
            config=_config(),
            snapshot_hash="c" * 64,
            chunk_hash="d" * 64,
        )
    reopened = SQLiteVectorSidecar.open(path)
    assert reopened.manifest == original_manifest
    assert reopened.records == tuple(sorted(_records(), key=lambda item: item.chunk_id))
    reopened.close()


def test_missing_or_deleted_vector_sidecar_does_not_affect_lexical_index(tmp_path: Path) -> None:
    snapshot = load_snapshot(
        FIXTURE / "content", FIXTURE / "graph.json", FIXTURE / "references.yml"
    )
    lexical_path = tmp_path / "lexical"
    lexical = SQLiteIndex.build(snapshot, lexical_path)
    lexical.close()
    vector_path = tmp_path / "vectors"
    vectors = SQLiteVectorSidecar.build(
        _records(), vector_path, config=_config(), snapshot_hash=HASH_A, chunk_hash=HASH_B
    )
    vectors.close()
    (vector_path / "vector.sqlite").unlink()
    (vector_path / "vector-manifest.json").unlink()

    reopened = SQLiteIndex.open(lexical_path)
    assert reopened.search("teaches", top_k=1)
    reopened.close()
    with pytest.raises(VectorSidecarError, match="does not exist"):
        SQLiteVectorSidecar.open(vector_path)


def test_file_paths_use_independent_manifests(tmp_path: Path) -> None:
    first_path = tmp_path / "first.db"
    second_path = tmp_path / "second.db"
    first = SQLiteVectorSidecar.build(
        _records(), first_path, config=_config(), snapshot_hash=HASH_A, chunk_hash=HASH_B
    )
    second = SQLiteVectorSidecar.build(
        _records(), second_path, config=_config(), snapshot_hash=HASH_A, chunk_hash=HASH_B
    )
    first.close()
    second.close()
    assert (tmp_path / "first.db.manifest.json").is_file()
    assert (tmp_path / "second.db.manifest.json").is_file()
    SQLiteVectorSidecar.open(first_path).close()
    SQLiteVectorSidecar.open(second_path).close()
