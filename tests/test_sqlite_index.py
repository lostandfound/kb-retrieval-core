from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from kb_retrieval_core import Entity, SQLiteIndex, SQLiteIndexError, Snapshot, chunk_snapshot, load_snapshot
from kb_retrieval_core._normalization import normalize_json
import kb_retrieval_core.sqlite_index as sqlite_index_module


FIXTURE = Path(__file__).parent / "fixtures" / "acceptance"


def _snapshot():
    return load_snapshot(FIXTURE / "content", FIXTURE / "graph.json", FIXTURE / "references.yml")


def test_sqlite_index_round_trips_snapshot_and_search(tmp_path: Path) -> None:
    snapshot = _snapshot()
    index = SQLiteIndex.build(snapshot, tmp_path / ".retrieval")
    assert (tmp_path / ".retrieval" / "index.sqlite").is_file()
    assert (tmp_path / ".retrieval" / "manifest.json").is_file()
    before = index.search_chunks("teaches", top_k=3)
    index.close()

    reopened = SQLiteIndex.open(tmp_path / ".retrieval")
    after = reopened.search_chunks("teaches", top_k=3)
    assert after == before
    assert [item.entity_path for item in reopened.snapshot.entities] == [item.entity_path for item in snapshot.entities]
    assert [item.chunk_id for item in reopened.snapshot.chunks] == [item.chunk_id for item in chunk_snapshot(snapshot).chunks]
    assert [(item.claim_path, item.status, item.confidence) for item in reopened.snapshot.claims] == [
        (item.claim_path, item.status, item.confidence) for item in snapshot.claims
    ]
    assert [item.reference_id for item in reopened.snapshot.references] == [item.reference_id for item in snapshot.references]
    assert reopened.snapshot.entities == snapshot.entities
    assert reopened.snapshot.chunks == chunk_snapshot(snapshot).chunks
    assert reopened.snapshot.relations == snapshot.relations
    assert reopened.snapshot.references == snapshot.references
    assert [_claim_signature(item) for item in reopened.snapshot.claims] == [
        _claim_signature(item) for item in snapshot.claims
    ]
    reopened.close()


def test_manifest_is_deterministic_and_records_source_fingerprint(tmp_path: Path) -> None:
    snapshot = _snapshot()
    first = SQLiteIndex.build(snapshot, tmp_path / "one")
    second = SQLiteIndex.build(snapshot, tmp_path / "two")
    assert first.manifest == second.manifest
    manifest = json.loads((tmp_path / "one" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["format"] == "kb-retrieval-sqlite"
    assert manifest["format_version"] == 1
    assert manifest["counts"]["claims"] == len(snapshot.claims)
    assert manifest["chunk_hash"]
    first.close()
    second.close()


def test_sqlite_index_accepts_database_file_path(tmp_path: Path) -> None:
    database = tmp_path / "custom.db"
    index = SQLiteIndex.build(_snapshot(), database, ngram_size=3)
    index.close()
    reopened = SQLiteIndex.open(database)
    assert reopened.ngram_size == 3
    assert reopened.search("teaches", top_k=1)[0].evidence.entity_path == "/entities/source.md"
    reopened.close()


def test_index_can_be_checked_against_source_snapshot(tmp_path: Path) -> None:
    snapshot = _snapshot()
    index = SQLiteIndex.build(snapshot, tmp_path / ".retrieval")
    assert index.matches_snapshot(snapshot)
    assert not index.needs_rebuild(snapshot)
    index.close()


def test_changed_source_requires_rebuild_and_rebuild_replaces_old_data(tmp_path: Path) -> None:
    snapshot = _snapshot()
    path = tmp_path / ".retrieval"
    index = SQLiteIndex.build(snapshot, path)
    changed_entity = replace(snapshot.entities[0], content=snapshot.entities[0].content + "\n追加情報")
    changed = replace(snapshot, entities=(changed_entity, *snapshot.entities[1:]), chunks=())
    assert index.needs_rebuild(changed)
    index.close()

    rebuilt = SQLiteIndex.build(changed, path)
    assert rebuilt.matches_snapshot(changed)
    assert "追加情報" in rebuilt.snapshot.entities[0].content
    rebuilt.close()


def test_failed_rebuild_keeps_previous_database_readable(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / ".retrieval"
    original = SQLiteIndex.build(_snapshot(), path)
    original_manifest = original.manifest
    original.close()

    def fail_write(*_args, **_kwargs):
        raise RuntimeError("injected build failure")

    monkeypatch.setattr(sqlite_index_module, "_write_snapshot", fail_write)
    with pytest.raises(RuntimeError, match="injected build failure"):
        SQLiteIndex.build(_snapshot(), path)

    reopened = SQLiteIndex.open(path)
    assert reopened.manifest == original_manifest
    assert reopened.search("teaches", top_k=1)
    reopened.close()


def test_open_rejects_schema_version_and_manifest_mismatch(tmp_path: Path) -> None:
    path = tmp_path / ".retrieval"
    index = SQLiteIndex.build(_snapshot(), path)
    index.close()
    database = path / "index.sqlite"

    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA user_version = 999")
    with pytest.raises(SQLiteIndexError, match="unsupported SQLite schema version"):
        SQLiteIndex.open(path)

    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA user_version = 1")
    manifest_path = path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_hash"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(SQLiteIndexError, match="does not match"):
        SQLiteIndex.open(path)


def test_open_without_sidecar_uses_versioned_manifest_in_database(tmp_path: Path) -> None:
    path = tmp_path / ".retrieval"
    index = SQLiteIndex.build(_snapshot(), path)
    expected = index.manifest
    index.close()
    (path / "manifest.json").unlink()
    reopened = SQLiteIndex.open(path)
    assert reopened.manifest == expected
    reopened.close()


def test_open_rejects_corrupt_sidecar_and_incomplete_database(tmp_path: Path) -> None:
    path = tmp_path / ".retrieval"
    index = SQLiteIndex.build(_snapshot(), path)
    index.close()
    manifest_path = path / "manifest.json"
    manifest_path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(SQLiteIndexError, match="invalid manifest"):
        SQLiteIndex.open(path)

    manifest_path.unlink()
    with sqlite3.connect(path / "index.sqlite") as connection:
        connection.execute("DELETE FROM chunks WHERE rowid IN (SELECT rowid FROM chunks LIMIT 1)")
    with pytest.raises(SQLiteIndexError, match="does not match manifest count"):
        SQLiteIndex.open(path)


def test_persisted_index_searches_japanese_without_whitespace(tmp_path: Path) -> None:
    snapshot = Snapshot(
        entities=(
            Entity(
                "/people/example.md",
                "Person",
                "宮城長順",
                content="# 経歴\n東恩納寛量に師事した。",
            ),
        )
    )
    path = tmp_path / ".retrieval"
    built = SQLiteIndex.build(snapshot, path)
    before = built.search("東恩納寛量", top_k=1)
    built.close()
    reopened = SQLiteIndex.open(path)
    after = reopened.search("東恩納寛量", top_k=1)
    assert after == before
    assert after[0].evidence.entity_path == "/people/example.md"
    reopened.close()


def _claim_signature(claim) -> tuple[object, ...]:
    return (
        claim.claim_id,
        claim.statement,
        claim.status,
        claim.confidence,
        claim.source_ids,
        claim.subject,
        claim.predicate,
        claim.target,
        claim.claim_path,
        claim.property,
        claim.value,
        json.dumps(normalize_json(claim.metadata), ensure_ascii=False, sort_keys=True),
    )
