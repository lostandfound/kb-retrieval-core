"""Load the public Markdown, graph, and reference interchange contract."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from .models import Claim, Entity, Reference, Relation, Snapshot

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    yaml = None  # type: ignore[assignment]
    _YAML_IMPORT_ERROR = exc
else:
    _YAML_IMPORT_ERROR = None


class SnapshotLoadError(ValueError):
    """Raised when source input is missing or invalid."""


def load_snapshot(content_root: str | Path, graph_path: str | Path, references_path: str | Path, eval_path: str | Path | None = None) -> Snapshot:
    root = _directory(content_root, "content root")
    graph_file = _file(graph_path, "graph.json")
    refs_file = _file(references_path, "references.yml")
    eval_file = None if eval_path is None else _file(eval_path, "evals/rag-eval.yml")
    graph = _load_json(graph_file)
    entities, markdown_claims = _load_markdown(root)
    nodes, edge_records, graph_claim_records = _graph_parts(graph, graph_file)
    entity_paths = {entity.entity_path for entity in entities}
    _validate_nodes(nodes, {entity.entity_path: entity for entity in entities}, graph_file)
    graph_relations = [_parse_relation(record, graph_file, i, entity_paths) for i, record in enumerate(edge_records)]
    markdown_relations = tuple(relation for entity in entities for relation in entity.relations)
    _validate_relations(markdown_relations, graph_relations, graph_file)
    # Markdown is the source of truth.  The graph is only a synchronized export;
    # retaining its parsed records here would lose owner/source provenance and
    # would make stale exports look like additional knowledge.
    relations = _dedupe_relations(markdown_relations, entities)
    graph_claims = [_parse_claim(record, graph_file, i, require_path=True) for i, record in enumerate(graph_claim_records)]
    _validate_claims(markdown_claims, graph_claims, graph_file)
    claims = tuple(markdown_claims)
    references = _load_references(refs_file)
    metadata = {} if eval_file is None else {"eval_path": str(eval_file)}
    try:
        return Snapshot(entities=entities, relations=relations, claims=claims, references=references, metadata=metadata)
    except (TypeError, ValueError) as exc:
        raise SnapshotLoadError(f"snapshot structure is invalid: {exc}") from exc


def _directory(value: str | Path, label: str) -> Path:
    path = Path(value)
    if not path.exists():
        raise SnapshotLoadError(f"{label} does not exist: {path}")
    if not path.is_dir():
        raise SnapshotLoadError(f"{label} is not a directory: {path}")
    return path


def _file(value: str | Path, label: str) -> Path:
    path = Path(value)
    if not path.exists():
        raise SnapshotLoadError(f"{label} does not exist: {path}")
    if not path.is_file():
        raise SnapshotLoadError(f"{label} is not a file: {path}")
    return path


def _load_markdown(root: Path) -> tuple[tuple[Entity, ...], tuple[Claim, ...]]:
    entities: list[Entity] = []
    claims: list[Claim] = []
    for path in sorted(root.rglob("*.md"), key=lambda item: item.relative_to(root).as_posix()):
        if not path.is_file():
            continue
        relative = "/" + path.relative_to(root).as_posix()
        data, body = _frontmatter(path)
        entity_type = _string_value(data, ("type", "entity_type"), path, required=True)
        if entity_type == "Index":
            continue
        if entity_type == "Claim":
            _validate_common_frontmatter(data, path, claim=True)
            claims.append(_parse_claim({**data, "path": relative}, path, 0, require_path=True))
            continue
        _validate_common_frontmatter(data, path, claim=False)
        declared = data.get("path", data.get("entity_path"))
        if declared is not None and declared != relative:
            raise SnapshotLoadError(f"{path}: frontmatter path {declared!r} does not match {relative!r}")
        title = _string_value(data, ("title",), path, required=True)
        description = _string_value(data, ("description",), path, required=True)
        tags = _string_list(data["tags"], path, "tags")
        source_ids = _source_list(data.get("sources", data.get("source_ids", ())), path, "sources")
        aliases = _string_list(data.get("aliases", ()), path, "aliases")
        raw_relations = data.get("relations", ())
        if raw_relations is None:
            raw_relations = ()
        if not isinstance(raw_relations, (list, tuple)):
            raise SnapshotLoadError(f"{path}: relations must be a list")
        relations = tuple(_parse_relation(record, path, i, {relative}, owner_path=relative) for i, record in enumerate(raw_relations) if isinstance(record, Mapping))
        if len(relations) != len(raw_relations):
            raise SnapshotLoadError(f"{path}: relations must contain mappings")
        raw_claims = data.get("claims", ())
        if raw_claims is None:
            raw_claims = ()
        if not isinstance(raw_claims, (list, tuple)):
            raise SnapshotLoadError(f"{path}: claims must be a list")
        nested_claims = tuple(_parse_claim({**record, "path": record.get("path", relative)}, path, i, require_path=False) for i, record in enumerate(raw_claims) if isinstance(record, Mapping))
        if len(nested_claims) != len(raw_claims):
            raise SnapshotLoadError(f"{path}: claims must contain mappings")
        recognized = {"type", "entity_type", "title", "description", "tags", "sources", "source_ids", "aliases", "relations", "claims", "path", "entity_path", "timestamp"}
        metadata = {key: value for key, value in data.items() if key not in recognized}
        try:
            timestamp = normalize_json(data["timestamp"])
            entities.append(Entity(relative, entity_type, title, tags, source_ids, relations, nested_claims, aliases, description, metadata, body, timestamp))
        except (TypeError, ValueError) as exc:
            raise SnapshotLoadError(f"{path}: invalid entity: {exc}") from exc
    if not entities:
        raise SnapshotLoadError(f"{root}: no retrievable Markdown entities found")
    return tuple(entities), tuple(claims)


def _frontmatter(path: Path) -> tuple[dict[str, Any], str]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise SnapshotLoadError(f"{path}: cannot read Markdown file: {exc}") from exc
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SnapshotLoadError(f"{path}: missing YAML frontmatter")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() in {"---", "..."})
    except StopIteration as exc:
        raise SnapshotLoadError(f"{path}: unterminated YAML frontmatter") from exc
    if yaml is None:
        raise SnapshotLoadError(f"{path}: YAML support requires PyYAML: {_YAML_IMPORT_ERROR}")
    try:
        value = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as exc:
        raise SnapshotLoadError(f"{path}: malformed YAML frontmatter: {exc}") from exc
    if not isinstance(value, dict):
        raise SnapshotLoadError(f"{path}: YAML frontmatter must be a mapping")
    body = "\n".join(lines[end + 1 :])
    if text.endswith(("\n", "\r")):
        body += "\n"
    return value, body


def _load_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        raise SnapshotLoadError(f"{path}: malformed JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc
    except OSError as exc:
        raise SnapshotLoadError(f"{path}: cannot read JSON: {exc}") from exc


def _graph_parts(value: Any, path: Path) -> tuple[tuple[Mapping[str, Any], ...], tuple[Mapping[str, Any], ...], tuple[Mapping[str, Any], ...]]:
    if not isinstance(value, Mapping):
        raise SnapshotLoadError(f"{path}: graph JSON must be an object")
    expected = {"nodes", "edges", "claims"}
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        details = []
        if missing:
            details.append(f"missing top-level collections {missing!r}")
        if extra:
            details.append(f"unknown top-level collections {extra!r}")
        raise SnapshotLoadError(
            f"{path}: graph JSON must contain exactly nodes, edges, and claims (" + "; ".join(details) + ")"
        )
    return (_records(value["nodes"], path, "nodes", True), _records(value["edges"], path, "edges", True), _records(value["claims"], path, "claims", True))


def _records(value: Any, path: Path, name: str, required: bool) -> tuple[Mapping[str, Any], ...]:
    if value is None and not required:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
        raise SnapshotLoadError(f"{path}: graph '{name}' must be a list of objects")
    return tuple(value)


def _validate_nodes(nodes: Iterable[Mapping[str, Any]], entities: Mapping[str, Entity], path: Path) -> None:
    seen: set[str] = set()
    for i, node in enumerate(nodes):
        node_path = _string_value(node, ("path", "entity_path", "id"), path, context=f"graph node {i}", required=True)
        if not node_path.startswith("/"):
            raise SnapshotLoadError(f"{path}: graph node {i} path must start with '/'")
        if node_path in seen:
            raise SnapshotLoadError(f"stale/inconsistent graph: {path}: duplicate graph node path {node_path!r}")
        entity = entities.get(node_path)
        if entity is None:
            raise SnapshotLoadError(f"stale/inconsistent graph: {path}: graph node path {node_path!r} has no Markdown entity")
        # The graph exporter must carry these fields.  They are checked against
        # the owning Markdown document rather than treated as an independent
        # source of truth.
        expected = {
            "type": entity.entity_type,
            "title": entity.title,
            "description": entity.description,
            "tags": list(entity.tags),
        }
        aliases = {"entity_type": "type"}
        for field, expected_value in expected.items():
            present = field if field in node else next((alias for alias, target in aliases.items() if target == field and alias in node), None)
            if present is None:
                raise SnapshotLoadError(
                    f"stale/inconsistent graph: {path}: graph node {node_path!r} is missing exported field {field!r}"
                )
            actual = node[present]
            if field == "tags":
                if not isinstance(actual, (list, tuple)):
                    raise SnapshotLoadError(f"stale/inconsistent graph: {path}: graph node {node_path!r} field 'tags' does not match Markdown")
                actual = list(actual)
            if actual != expected_value:
                raise SnapshotLoadError(
                    f"stale/inconsistent graph: {path}: graph node {node_path!r} field {field!r} differs from Markdown "
                    f"(graph={actual!r}, markdown={expected_value!r})"
                )
        seen.add(node_path)


def _parse_relation(record: Mapping[str, Any], path: Path, index: int, entity_paths: set[str], owner_path: str | None = None) -> Relation:
    context = f"graph edge {index}" if path.suffix == ".json" else f"relation {index}"
    predicate = _string_value(record, ("predicate", "relation", "type"), path, context, True)
    target = _string_value(record, ("target", "to"), path, context, True)
    source_path = _string_value(record, ("source", "source_path", "from"), path, context, path.suffix == ".json")
    if owner_path is not None:
        if source_path is not None and source_path != owner_path:
            raise SnapshotLoadError(
                f"stale/inconsistent graph: {path}: {context} source path {source_path!r} conflicts with Markdown owner {owner_path!r}"
            )
        source_path = owner_path
    source_ids = _source_list(record.get("source_ids", record.get("sources", ())), path, f"{context} source_ids")
    owner_ids = _source_list(record.get("owner_source_ids", ()), path, f"{context} owner_source_ids")
    confidence = record.get("confidence")
    if confidence is not None and (not isinstance(confidence, str) or not confidence.strip()):
        raise SnapshotLoadError(f"{path}: {context} confidence must be a non-empty string")
    if source_path is not None and source_path not in entity_paths and source_path.startswith("/"):
        raise SnapshotLoadError(f"{path}: {context} source path {source_path!r} has no Markdown entity")
    recognized = {"predicate", "relation", "type", "target", "to", "source", "source_path", "from", "source_ids", "sources", "owner_source_ids", "confidence"}
    return Relation(predicate, target, source_ids, source_path, {k: v for k, v in record.items() if k not in recognized}, confidence, owner_ids)


def _parse_claim(record: Mapping[str, Any], path: Path, index: int, require_path: bool) -> Claim:
    context = f"claim {index}"
    claim_path = record.get("path", record.get("claim_path"))
    if require_path and (not isinstance(claim_path, str) or not claim_path.startswith("/")):
        raise SnapshotLoadError(f"{path}: {context} requires root-relative claim path")
    if require_path:
        for field_name in ("status", "confidence"):
            _string_value(record, (field_name,), path, context, required=True)
        if "sources" not in record and "source_ids" not in record:
            raise SnapshotLoadError(f"{path}: {context} requires sources")
        if not _source_list(record.get("sources", record.get("source_ids")), path, f"{context} sources"):
            raise SnapshotLoadError(f"{path}: {context} sources must not be empty")
    confidence = record.get("confidence")
    if confidence is not None and (not isinstance(confidence, str) or not confidence.strip()):
        raise SnapshotLoadError(f"{path}: {context} confidence must be a non-empty string")
    recognized = {"path", "claim_path", "id", "claim_id", "statement", "status", "confidence", "sources", "source_ids", "subject", "predicate", "object", "target", "property", "value"}
    try:
        return Claim(claim_id=record.get("claim_id", record.get("id")), statement=record.get("statement"), status=record.get("status"), confidence=confidence, source_ids=_source_list(record.get("sources", record.get("source_ids", ())), path, f"{context} sources"), subject=record.get("subject"), predicate=record.get("predicate"), target=record.get("object", record.get("target")), metadata={k: v for k, v in record.items() if k not in recognized}, claim_path=claim_path, property=record.get("property"), value=record.get("value"))
    except (TypeError, ValueError) as exc:
        raise SnapshotLoadError(f"{path}: invalid {context}: {exc}") from exc


def _relation_key(relation: Relation) -> tuple[str | None, str, str, str | None]:
    """The complete exported edge identity, including optional confidence."""
    return (relation.source_path, relation.predicate, relation.target, relation.confidence)


def _relation_base_key(relation: Relation) -> tuple[str | None, str, str]:
    return (relation.source_path, relation.predicate, relation.target)


def _validate_relations(markdown: Iterable[Relation], graph: Iterable[Relation], path: Path) -> None:
    """Ensure graph edges are an exact, order-independent Markdown export."""
    markdown = tuple(markdown)
    graph = tuple(graph)

    def index(records: tuple[Relation, ...], label: str) -> dict[tuple[str | None, str, str, str | None], Relation]:
        result: dict[tuple[str | None, str, str, str | None], Relation] = {}
        by_base: dict[tuple[str | None, str, str], list[Relation]] = {}
        for relation in records:
            key = _relation_key(relation)
            if key in result:
                raise SnapshotLoadError(
                    f"stale/inconsistent graph: {path}: duplicate {label} edge "
                    f"{key!r}"
                )
            result[key] = relation
            by_base.setdefault(_relation_base_key(relation), []).append(relation)
        for base, variants in by_base.items():
            if len(variants) > 1:
                raise SnapshotLoadError(
                    f"stale/inconsistent graph: {path}: conflicting {label} edges for "
                    f"{base!r}; confidence values are {[item.confidence for item in variants]!r}"
                )
        return result

    markdown_by_key = index(markdown, "Markdown")
    graph_by_key = index(graph, "graph")
    markdown_keys = set(markdown_by_key)
    graph_keys = set(graph_by_key)
    if markdown_keys == graph_keys:
        return

    markdown_by_base = {_relation_base_key(item): item for item in markdown}
    graph_by_base = {_relation_base_key(item): item for item in graph}
    conflicts = sorted(
        base for base in set(markdown_by_base) & set(graph_by_base)
        if _relation_key(markdown_by_base[base]) != _relation_key(graph_by_base[base])
    )
    if conflicts:
        details = "; ".join(
            f"{base!r}: Markdown confidence={markdown_by_base[base].confidence!r}, "
            f"graph confidence={graph_by_base[base].confidence!r}"
            for base in conflicts
        )
        raise SnapshotLoadError(f"stale/inconsistent graph: {path}: conflicting edge records ({details})")
    missing = sorted(markdown_keys - graph_keys, key=repr)
    extra = sorted(graph_keys - markdown_keys, key=repr)
    parts = []
    if missing:
        parts.append(f"missing graph edges for Markdown records {missing!r}")
    if extra:
        parts.append(f"extra graph edges not present in Markdown {extra!r}")
    raise SnapshotLoadError(f"stale/inconsistent graph: {path}: " + "; ".join(parts))


def _claim_export_key(claim: Claim) -> tuple[object, ...]:
    """Fields emitted by graph.json for a Claim, in canonical form."""
    return (
        claim.claim_path,
        claim.subject,
        claim.status,
        claim.confidence,
        tuple(claim.source_ids),
        claim.predicate,
        claim.target,
        claim.property,
        normalize_json(claim.value) if claim.value is not None else None,
    )


def _validate_claims(markdown: Iterable[Claim], graph: Iterable[Claim], path: Path) -> None:
    """Ensure graph Claims exactly mirror Claim Markdown exports."""
    markdown = tuple(markdown)
    graph = tuple(graph)

    def index(records: tuple[Claim, ...], label: str) -> dict[str, Claim]:
        result: dict[str, Claim] = {}
        for claim in records:
            key = claim.claim_path
            if key is None:
                raise SnapshotLoadError(f"stale/inconsistent graph: {path}: {label} Claim has no path")
            if key in result:
                raise SnapshotLoadError(f"stale/inconsistent graph: {path}: duplicate {label} Claim path {key!r}")
            result[key] = claim
        return result

    markdown_by_path = index(markdown, "Markdown")
    graph_by_path = index(graph, "graph")
    missing = sorted(set(markdown_by_path) - set(graph_by_path))
    extra = sorted(set(graph_by_path) - set(markdown_by_path))
    conflicts = sorted(
        key for key in set(markdown_by_path) & set(graph_by_path)
        if _claim_export_key(markdown_by_path[key]) != _claim_export_key(graph_by_path[key])
    )
    if conflicts:
        details = "; ".join(
            f"{key!r}: Markdown={_claim_export_key(markdown_by_path[key])!r}, "
            f"graph={_claim_export_key(graph_by_path[key])!r}"
            for key in conflicts
        )
        raise SnapshotLoadError(f"stale/inconsistent graph: {path}: conflicting Claim records ({details})")
    parts = []
    if missing:
        parts.append(f"missing graph Claims for Markdown paths {missing!r}")
    if extra:
        parts.append(f"extra graph Claims not present in Markdown paths {extra!r}")
    if parts:
        raise SnapshotLoadError(f"stale/inconsistent graph: {path}: " + "; ".join(parts))


def _dedupe_relations(relations: Iterable[Relation], entities: Iterable[Entity]) -> tuple[Relation, ...]:
    owners = {entity.entity_path: entity.source_ids for entity in entities}
    merged: dict[tuple[object, ...], Relation] = {}
    for relation in relations:
        key = (relation.source_path, relation.predicate, relation.target, relation.confidence)
        owner = relation.owner_source_ids or owners.get(relation.source_path or "", ())
        existing = merged.get(key)
        if existing is None:
            merged[key] = Relation(relation.predicate, relation.target, relation.source_ids, relation.source_path, relation.metadata, relation.confidence, owner)
        else:
            merged[key] = Relation(existing.predicate, existing.target, tuple(dict.fromkeys(existing.source_ids + relation.source_ids)), existing.source_path, existing.metadata, existing.confidence, tuple(dict.fromkeys(existing.owner_source_ids + owner)))
    return tuple(merged[key] for key in sorted(merged, key=lambda item: tuple(str(part) for part in item)))


def _dedupe_claims(claims: Iterable[Claim]) -> tuple[Claim, ...]:
    merged: dict[str, Claim] = {}
    for claim in claims:
        key = claim.claim_path or claim.claim_id or repr(claim)
        if key not in merged:
            merged[key] = claim
            continue
        old = merged[key]
        merged[key] = Claim(old.claim_id or claim.claim_id, old.statement or claim.statement, old.status or claim.status, old.confidence or claim.confidence, tuple(dict.fromkeys(old.source_ids + claim.source_ids)), old.subject or claim.subject, old.predicate or claim.predicate, old.target or claim.target, {**old.metadata, **claim.metadata}, old.claim_path or claim.claim_path, old.property or claim.property, old.value if old.value is not None else claim.value)
    return tuple(merged[key] for key in sorted(merged))


def _load_references(path: Path) -> tuple[Reference, ...]:
    if yaml is None:
        raise SnapshotLoadError(f"{path}: YAML support requires PyYAML: {_YAML_IMPORT_ERROR}")
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except yaml.YAMLError as exc:
        raise SnapshotLoadError(f"{path}: malformed YAML: {exc}") from exc
    if isinstance(value, Mapping) and set(value) == {"references"}:
        value = value["references"]
    if not isinstance(value, Mapping):
        raise SnapshotLoadError(f"{path}: references YAML must be an ID-keyed mapping")
    result: list[Reference] = []
    for raw_id, record in sorted(value.items(), key=lambda pair: str(pair[0])):
        if not isinstance(raw_id, str) or not isinstance(record, Mapping):
            raise SnapshotLoadError(f"{path}: reference IDs and records must be mappings")
        reference_type = record.get("type")
        if not isinstance(reference_type, str) or not reference_type.strip():
            raise SnapshotLoadError(f"{path}: reference {raw_id!r} requires non-empty type")
        reference_id = normalize_source_id(raw_id)
        title = record.get("title")
        if not isinstance(title, str) or not title.strip():
            raise SnapshotLoadError(f"{path}: reference {raw_id!r} requires non-empty title")
        author = record.get("author")
        authors = record.get("authors", ())
        if author is not None and not isinstance(author, str):
            raise SnapshotLoadError(f"{path}: reference {raw_id!r} author must be a string")
        if isinstance(authors, str):
            authors = (authors,)
        if not isinstance(authors, (list, tuple)) or any(not isinstance(item, str) for item in authors):
            raise SnapshotLoadError(f"{path}: reference {raw_id!r} authors must be a string list")
        year = record.get("year")
        if year is not None and (isinstance(year, bool) or not isinstance(year, int)):
            raise SnapshotLoadError(f"{path}: reference {raw_id!r} year must be an integer")
        url = record.get("url")
        if url is not None and (not isinstance(url, str) or not url.strip()):
            raise SnapshotLoadError(f"{path}: reference {raw_id!r} url must be a non-empty string")
        if reference_type.casefold() == "web" and url is None:
            raise SnapshotLoadError(f"{path}: web reference {raw_id!r} requires non-empty url")
        metadata = {k: normalize_json(v) for k, v in record.items() if k not in {"title", "author", "authors", "year", "url"}}
        try:
            result.append(Reference(reference_id, title, tuple(authors), year, url, metadata, author=author))
        except (TypeError, ValueError) as exc:
            raise SnapshotLoadError(f"{path}: invalid reference {raw_id!r}: {exc}") from exc
    return tuple(result)


def normalize_source_id(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("source ID must be a string")
    result = value.strip()
    if result.casefold().startswith("ref:"):
        result = result[4:].strip()
    if not result:
        raise ValueError("source ID must not be empty")
    return result


def _source_list(value: Any, path: Path, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = (value,)
    if not isinstance(value, (list, tuple)):
        raise SnapshotLoadError(f"{path}: {field_name} must be a string or list of strings")
    try:
        return tuple(normalize_source_id(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise SnapshotLoadError(f"{path}: {field_name} contains invalid source ID: {exc}") from exc


def _string_list(value: Any, path: Path, field_name: str) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise SnapshotLoadError(f"{path}: {field_name} must be a list of strings")
    if any(not isinstance(item, str) for item in value):
        raise SnapshotLoadError(f"{path}: {field_name} must contain only strings")
    return tuple(value)


def _validate_common_frontmatter(data: Mapping[str, Any], path: Path, *, claim: bool) -> None:
    """Validate fields required on persisted Markdown documents.

    ``sources`` is intentionally not required for ordinary entities here:
    whether an ordinary type requires sources is owned by ``vocabulary.yml``
    and enforced by the harness.  Claim documents do require non-empty sources
    because their provenance is part of the public Claim contract.
    """

    _string_value(data, ("title",), path, required=True)
    _string_value(data, ("description",), path, required=True)
    if "tags" not in data:
        raise SnapshotLoadError(f"{path}: frontmatter missing required field 'tags'")
    _string_list(data["tags"], path, "tags")
    if "timestamp" not in data:
        raise SnapshotLoadError(f"{path}: frontmatter missing required field 'timestamp'")
    timestamp = normalize_json(data["timestamp"])
    if not isinstance(timestamp, str) or not timestamp.strip():
        raise SnapshotLoadError(f"{path}: frontmatter field 'timestamp' must be a non-empty date/time value")
    if claim:
        for field_name in ("subject", "status", "confidence"):
            _string_value(data, (field_name,), path, required=True)
        if "sources" not in data:
            raise SnapshotLoadError(f"{path}: frontmatter missing required field 'sources'")
        source_ids = _source_list(data["sources"], path, "sources")
        if not source_ids:
            raise SnapshotLoadError(f"{path}: frontmatter field 'sources' must not be empty")


def _string_value(mapping: Mapping[str, Any], aliases: tuple[str, ...], path: Path, context: str = "frontmatter", required: bool = False) -> str | None:
    present = [key for key in aliases if key in mapping]
    if len(present) > 1 and mapping[present[0]] != mapping[present[1]]:
        raise SnapshotLoadError(f"{path}: {context} conflicting aliases {present}")
    if not present:
        if required:
            raise SnapshotLoadError(f"{path}: {context} missing required field {aliases[0]!r}")
        return None
    value = mapping[present[0]]
    if not isinstance(value, str) or not value.strip():
        raise SnapshotLoadError(f"{path}: {context} field {aliases[0]!r} must be a non-empty string")
    return value


def normalize_json(value: object) -> object:
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


load = load_snapshot

__all__ = ["SnapshotLoadError", "load", "load_snapshot", "normalize_json", "normalize_source_id"]
