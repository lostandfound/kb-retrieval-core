import json
import inspect
from pathlib import Path

import pytest

from kb_retrieval_core import (
    Claim,
    SnapshotLoadError,
    chunk_snapshot,
    load_snapshot,
    normalize_json,
    normalize_source_id,
)
from kb_retrieval_core._normalization import normalize_source_id as shared_normalize_source_id
from kb_retrieval_core.snapshot import _claim_export_key


def test_public_source_normalizer_signature_and_errors_remain_stable() -> None:
    assert list(inspect.signature(normalize_source_id).parameters) == ["value"]
    assert normalize_source_id("  ref: source  ") == "source"
    with pytest.raises(TypeError, match="source ID must be a string"):
        normalize_source_id(1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="source ID must not be empty"):
        normalize_source_id("  ")


def test_shared_normalizers_cover_internal_compatibility_cases() -> None:
    assert shared_normalize_source_id("  ref:  ", allow_empty=True) == ""
    assert normalize_json({"values": {"b", "a"}}) == {"values": ["a", "b"]}
    with pytest.raises(TypeError, match="unsupported JSON value"):
        normalize_json(object())
    with pytest.raises(ValueError, match="non-finite number"):
        normalize_json(float("inf"))


def _bundle(tmp_path: Path) -> tuple[Path, Path, Path]:
    content = tmp_path / "content"
    content.mkdir()
    (content / "source.md").write_text("""---
type: Person
title: Source
description: Source entity
tags: [fixture]
timestamp: 2026-01-01T00:00:00Z
sources: ["ref:source"]
relations:
  - predicate: knows
    target: /target.md
---

# Section

Source body.
""", encoding="utf-8")
    (content / "target.md").write_text("""---
type: Note
title: Target
description: Target note
tags: [fixture]
timestamp: 2026-01-01T00:00:00Z
sources: [target]
---

Target body.
""", encoding="utf-8")
    (content / "index.md").write_text("""---
type: Index
title: Index
description: Navigation
tags: [fixture]
timestamp: 2026-01-01T00:00:00Z
---

Index.
""", encoding="utf-8")
    (content / "claim.md").write_text("""---
type: Claim
title: Year claim
description: Claim fixture
tags: [fixture]
timestamp: 2026-01-01T00:00:00Z
subject: /source.md
property: year
value: "1900"
status: accepted
confidence: A
sources: [ref:source]
---

Claim body.
""", encoding="utf-8")
    graph = tmp_path / "graph.json"
    graph.write_text(json.dumps({"nodes": [{"path": "/source.md", "type": "Person", "title": "Source", "description": "Source entity", "tags": ["fixture"]}], "edges": [{"source": "/source.md", "target": "/target.md", "predicate": "knows"}], "claims": [{"path": "/claim.md", "subject": "/source.md", "property": "year", "value": "1900", "status": "accepted", "confidence": "A", "sources": ["ref:source"]}]}), encoding="utf-8")
    references = tmp_path / "references.yml"
    references.write_text("""source:
  type: book
  title: Source
  author: Author
  checked: 2026-01-02
target:
  type: book
  title: Target
  authors: [Target Author]
""", encoding="utf-8")
    return content, graph, references


def test_load_snapshot_normalizes_sources_excludes_index_and_keeps_claims(tmp_path: Path) -> None:
    content, graph, references = _bundle(tmp_path)
    snapshot = load_snapshot(content, graph, references)
    assert [entity.entity_path for entity in snapshot.entities] == ["/source.md", "/target.md"]
    assert snapshot.entities[0].source_ids == ("source",)
    assert snapshot.relations[0].owner_source_ids == ("source",)
    assert snapshot.claims[0].property == "year"
    assert snapshot.references[0].authors == ("Author",)
    assert snapshot.references[0].metadata["checked"] == "2026-01-02"
    assert len(chunk_snapshot(snapshot).chunks) == 2


def test_load_snapshot_is_deterministic_and_reports_malformed_inputs(tmp_path: Path) -> None:
    content, graph, references = _bundle(tmp_path)
    assert load_snapshot(content, graph, references) == load_snapshot(content, graph, references)
    graph.write_text("{bad", encoding="utf-8")
    with pytest.raises(SnapshotLoadError, match="malformed JSON"):
        load_snapshot(content, graph, references)


def test_load_snapshot_rejects_missing_optional_evaluation_file(tmp_path: Path) -> None:
    content, graph, references = _bundle(tmp_path)
    with pytest.raises(SnapshotLoadError, match="evals/rag-eval.yml"):
        load_snapshot(content, graph, references, tmp_path / "missing-rag-eval.yml")


@pytest.mark.parametrize(
    "payload",
    [
        {"nodes": [], "edges": []},
        {"nodes": [], "edges": [], "claims": [], "links": []},
        {"graph": {"nodes": [], "edges": [], "claims": []}},
    ],
)
def test_graph_json_requires_exact_top_level_collections(tmp_path: Path, payload: dict[str, object]) -> None:
    content, graph, references = _bundle(tmp_path)
    graph.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SnapshotLoadError, match="exactly nodes, edges, and claims"):
        load_snapshot(content, graph, references)


@pytest.mark.parametrize("missing", ("description", "tags", "timestamp"))
def test_markdown_common_fields_are_required(tmp_path: Path, missing: str) -> None:
    content, graph, references = _bundle(tmp_path)
    source = content / "source.md"
    source_text = source.read_text(encoding="utf-8")
    source_text = source_text.replace(f"{missing}: " + {"description": "Source entity", "tags": "[fixture]", "timestamp": "2026-01-01T00:00:00Z"}[missing] + "\n", "")
    source.write_text(source_text, encoding="utf-8")
    with pytest.raises(SnapshotLoadError, match=missing):
        load_snapshot(content, graph, references)


def test_markdown_common_field_types_are_validated(tmp_path: Path) -> None:
    content, graph, references = _bundle(tmp_path)
    source = content / "source.md"
    source.write_text(source.read_text(encoding="utf-8").replace("tags: [fixture]", "tags: fixture"), encoding="utf-8")
    with pytest.raises(SnapshotLoadError, match="tags"):
        load_snapshot(content, graph, references)


def test_claim_markdown_provenance_is_required(tmp_path: Path) -> None:
    content, graph, references = _bundle(tmp_path)
    claim = content / "claim.md"
    claim.write_text("\n".join([
        "---", "type: Claim", "title: Claim", "description: Claim description",
        "tags: [fixture]", "timestamp: 2026-01-01T00:00:00Z", "subject: /source.md",
        "status: proposed", "confidence: B", "predicate: knows", "object: /target.md",
        "---", "Claim body.", ""
    ]), encoding="utf-8")
    with pytest.raises(SnapshotLoadError, match="sources"):
        load_snapshot(content, graph, references)


@pytest.mark.parametrize("missing", ("description", "tags", "timestamp", "subject", "status", "confidence"))
def test_claim_markdown_required_fields_are_validated(tmp_path: Path, missing: str) -> None:
    content, graph, references = _bundle(tmp_path)
    claim = content / "claim.md"
    fields = {
        "description": "Claim description", "tags": "[fixture]",
        "timestamp": "2026-01-01T00:00:00Z", "subject": "/source.md",
        "status": "proposed", "confidence": "B", "sources": "[source]",
    }
    lines = ["---", "type: Claim", "title: Claim"]
    lines.extend(f"{key}: {value}" for key, value in fields.items() if key != missing)
    lines.extend(["predicate: knows", "object: /target.md", "---", "Claim body.", ""])
    claim.write_text("\n".join(lines), encoding="utf-8")
    with pytest.raises(SnapshotLoadError, match=missing):
        load_snapshot(content, graph, references)


def test_references_require_type_and_web_url(tmp_path: Path) -> None:
    content, graph, references = _bundle(tmp_path)
    references.write_text("source:\n  title: Source\n", encoding="utf-8")
    with pytest.raises(SnapshotLoadError, match="type"):
        load_snapshot(content, graph, references)
    references.write_text("source:\n  type: web\n  title: Source\n", encoding="utf-8")
    with pytest.raises(SnapshotLoadError, match="url"):
        load_snapshot(content, graph, references)


def test_graph_export_is_exactly_deduplicated_and_keeps_markdown_owner(tmp_path: Path) -> None:
    content, graph, references = _bundle(tmp_path)
    snapshot = load_snapshot(content, graph, references)
    assert len(snapshot.relations) == 1
    relation = snapshot.relations[0]
    assert relation.source_path == "/source.md"
    assert relation.owner_source_ids == ("source",)
    assert relation.source_ids == ()


def test_claim_export_key_contains_each_exported_field_once_for_both_forms() -> None:
    relation = Claim(
        claim_path="/claims/relation.md",
        subject="/source.md",
        status="proposed",
        confidence="D",
        source_ids=("claim",),
        predicate="teaches",
        target="/target.md",
    )
    value = Claim(
        claim_path="/claims/value.md",
        subject="/source.md",
        status="accepted",
        confidence="A",
        source_ids=("book",),
        property="founded-year",
        value="1900",
    )
    assert _claim_export_key(relation) == (
        "/claims/relation.md", "/source.md", "proposed", "D", ("claim",),
        "teaches", "/target.md", None, None,
    )
    assert _claim_export_key(value) == (
        "/claims/value.md", "/source.md", "accepted", "A", ("book",),
        None, None, "founded-year", "1900",
    )


def _rewrite_graph(graph: Path, transform) -> None:
    payload = json.loads(graph.read_text(encoding="utf-8"))
    transform(payload)
    graph.write_text(json.dumps(payload), encoding="utf-8")


def test_graph_node_export_field_mismatch_is_actionable(tmp_path: Path) -> None:
    content, graph, references = _bundle(tmp_path)
    _rewrite_graph(graph, lambda value: value["nodes"][0].update(title="Stale title"))
    with pytest.raises(SnapshotLoadError, match="stale/inconsistent graph.*title"):
        load_snapshot(content, graph, references)


@pytest.mark.parametrize(
    ("label", "transform", "message"),
    [
        ("missing", lambda value: value["edges"].clear(), "missing graph edges"),
        ("extra", lambda value: value["edges"].append({"source": "/source.md", "predicate": "extra", "target": "/target.md"}), "extra graph edges"),
        ("duplicate", lambda value: value["edges"].append(dict(value["edges"][0])), "duplicate graph edge"),
        ("conflict", lambda value: value["edges"][0].update(confidence="D"), "conflicting edge records"),
    ],
)
def test_graph_edge_synchronization_failures_are_actionable(tmp_path: Path, label: str, transform, message: str) -> None:
    content, graph, references = _bundle(tmp_path)
    _rewrite_graph(graph, transform)
    with pytest.raises(SnapshotLoadError, match=f"stale/inconsistent graph.*{message}"):
        load_snapshot(content, graph, references)


def test_duplicate_markdown_edges_are_rejected(tmp_path: Path) -> None:
    content, graph, references = _bundle(tmp_path)
    source = content / "source.md"
    text = source.read_text(encoding="utf-8")
    text = text.replace("    target: /target.md\n", "    target: /target.md\n  - predicate: knows\n    target: /target.md\n", 1)
    source.write_text(text, encoding="utf-8")
    with pytest.raises(SnapshotLoadError, match="stale/inconsistent graph.*duplicate Markdown edge"):
        load_snapshot(content, graph, references)


@pytest.mark.parametrize(
    ("transform", "message"),
    [
        (lambda value: value["claims"].pop(), "missing graph Claims"),
        (lambda value: value["claims"].append(dict(value["claims"][0], path="/claims/extra.md")), "extra graph Claims"),
        (lambda value: value["claims"].append(dict(value["claims"][0])), "duplicate graph Claim"),
        (lambda value: value["claims"][0].update(status="rejected"), "conflicting Claim records"),
    ],
)
def test_claim_synchronization_failures_are_actionable(tmp_path: Path, transform, message: str) -> None:
    content, graph, references = _bundle(tmp_path)
    _rewrite_graph(graph, transform)
    with pytest.raises(SnapshotLoadError, match=f"stale/inconsistent graph.*{message}"):
        load_snapshot(content, graph, references)
