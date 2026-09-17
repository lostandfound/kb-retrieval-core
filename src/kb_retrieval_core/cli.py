"""Small, offline command-line interface for the retrieval core.

The CLI deliberately exposes source loading, persistent index construction,
deterministic search, inspection, and evaluation only.  It does not generate
answers or call a model.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from ._normalization import normalize_json
from .chunking import chunk_snapshot, relation_to_dict
from .evaluation import EvaluationReport, evaluate, load_evaluation_cases
from .models import Claim, Entity, Reference, Relation, SearchHit
from .snapshot import load_snapshot
from .sqlite_index import SQLiteIndex


class _ArgumentError(ValueError):
    pass


class _JSONArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _ArgumentError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = _JSONArgumentParser(prog="kb-retrieval", description="Deterministic offline knowledge-base retrieval")
    commands = parser.add_subparsers(dest="command", required=True)

    build = commands.add_parser("build", help="load source files and build a persistent SQLite index")
    build.add_argument("--content-root", required=True, type=Path)
    build.add_argument("--graph", "--graph-path", dest="graph_path", required=True, type=Path)
    build.add_argument("--references", "--references-path", dest="references_path", required=True, type=Path)
    build.add_argument("--eval", "--eval-path", dest="eval_path", type=Path)
    _index_argument(build)
    build.add_argument("--ngram-size", type=int, default=2)
    _pretty_argument(build)

    search = commands.add_parser("search", help="search persisted chunks")
    search.add_argument("query")
    _index_argument(search, required=True)
    search.add_argument("--top-k", type=int, default=5)
    _pretty_argument(search)

    inspect = commands.add_parser("inspect", help="inspect a persisted entity and its chunks")
    inspect.add_argument("entity_path")
    _index_argument(inspect, required=True)
    _pretty_argument(inspect)

    evaluation = commands.add_parser("eval", help="evaluate retrieval against rag-eval.yml")
    evaluation.add_argument("--eval", "--eval-path", dest="eval_path", type=Path)
    _index_argument(evaluation, required=True)
    evaluation.add_argument("--k", "--top-k", dest="k", type=int, default=5)
    _pretty_argument(evaluation)
    return parser


def _index_argument(parser: argparse.ArgumentParser, *, required: bool = False) -> None:
    parser.add_argument("--index", "--index-dir", dest="index_path", default=".retrieval", required=required, type=Path)


def _pretty_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--pretty", action="store_true", help="indent JSON output")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        if args.command == "build":
            result = _build(args)
        elif args.command == "search":
            result = _search(args)
        elif args.command == "inspect":
            result = _inspect(args)
        elif args.command == "eval":
            result = _evaluate(args)
        else:  # pragma: no cover - argparse enforces the subcommands
            parser.error(f"unknown command {args.command!r}")
            return 2
        _write_json(result, pretty=args.pretty)
        return 0
    except Exception as exc:
        _write_json(
            {"error": str(exc)},
            stream=sys.stderr,
            pretty=getattr(locals().get("args"), "pretty", False),
        )
        return 2


def _build(args: argparse.Namespace) -> dict[str, object]:
    snapshot = load_snapshot(args.content_root, args.graph_path, args.references_path, args.eval_path)
    with SQLiteIndex.build(snapshot, args.index_path, ngram_size=args.ngram_size) as index:
        return {
            "command": "build",
            "index": str(index.database_path),
            "manifest": index.manifest,
        }


def _search(args: argparse.Namespace) -> dict[str, object]:
    with SQLiteIndex.open(args.index_path) as index:
        hits = index.search(args.query, top_k=args.top_k)
    return {"command": "search", "query": args.query, "results": [_hit_to_dict(hit) for hit in hits]}


def _inspect(args: argparse.Namespace) -> dict[str, object]:
    path = args.entity_path if args.entity_path.startswith("/") else "/" + args.entity_path
    with SQLiteIndex.open(args.index_path) as index:
        entity = next((item for item in index.snapshot.entities if item.entity_path == path), None)
        if entity is None:
            raise ValueError(f"entity not found: {path}")
        snapshot = index.snapshot
        chunks = tuple(item for item in snapshot.chunks if item.entity_path == path)
        if not chunks:
            chunks = tuple(item for item in chunk_snapshot(snapshot).chunks if item.entity_path == path)
    return {
        "command": "inspect",
        "entity": _entity_to_dict(entity),
        "chunks": [_chunk_to_dict(item) for item in chunks],
    }


def _evaluate(args: argparse.Namespace) -> dict[str, object]:
    with SQLiteIndex.open(args.index_path) as index:
        eval_path = args.eval_path
        if eval_path is None:
            source_metadata = index.manifest.get("source_metadata", {})
            if isinstance(source_metadata, dict):
                value = source_metadata.get("eval_path")
                if isinstance(value, str):
                    eval_path = Path(value)
        if eval_path is None:
            raise ValueError("evaluation path is required (pass --eval-path or build with --eval-path)")
        cases = load_evaluation_cases(eval_path)
        report = evaluate(cases, index.search, k=args.k)
    return {"command": "eval", **_report_to_dict(report)}


def _hit_to_dict(hit: SearchHit) -> dict[str, object]:
    evidence = hit.evidence
    return {
        "rank": hit.rank,
        "score": hit.score,
        "retriever": hit.retriever,
        "entity_path": evidence.entity_path,
        "section": evidence.section,
        "text": evidence.text,
        "source_ids": list(evidence.source_ids),
        "passage_source_ids": list(evidence.passage_source_ids or evidence.source_ids),
        "metadata": _json_value(evidence.metadata),
    }


def _entity_to_dict(entity: Entity) -> dict[str, object]:
    return {
        "entity_path": entity.entity_path,
        "type": entity.entity_type,
        "title": entity.title,
        "description": entity.description,
        "tags": list(entity.tags),
        "aliases": list(entity.aliases),
        "source_ids": list(entity.source_ids),
        "content": entity.content,
        "timestamp": entity.timestamp,
        "relations": [relation_to_dict(item) for item in entity.relations],
        "claims": [_claim_to_dict(item) for item in entity.claims],
        "metadata": _json_value(entity.metadata),
    }


def _chunk_to_dict(chunk: Any) -> dict[str, object]:
    return {
        "chunk_id": chunk.chunk_id,
        "entity_path": chunk.entity_path,
        "ordinal": chunk.ordinal,
        "heading": chunk.heading,
        "text": chunk.text,
        "content_hash": chunk.content_hash,
        "source_ids": list(chunk.source_ids),
    }


def _claim_to_dict(claim: Claim) -> dict[str, object]:
    return {
        "claim_id": claim.claim_id,
        "claim_path": claim.claim_path,
        "status": claim.status,
        "confidence": claim.confidence,
        "source_ids": list(claim.source_ids),
        "subject": claim.subject,
        "predicate": claim.predicate,
        "target": claim.target,
        "property": claim.property,
        "value": _json_value(claim.value),
        "metadata": _json_value(claim.metadata),
    }


def _report_to_dict(report: EvaluationReport) -> dict[str, object]:
    return {
        "k": report.k,
        "retrieval_mode": report.retrieval_mode,
        "snapshot_hash": report.snapshot_hash,
        "evaluation_case_hash": report.evaluation_case_hash,
        "package_identity": report.package_identity,
        "embedding_fingerprint": report.embedding_fingerprint,
        "vector_index_fingerprint": report.vector_index_fingerprint,
        "fusion_config": _json_value(report.fusion_config),
        "recall_at_k": report.recall_at_k,
        "mrr": report.mrr,
        "successes": sum(result.success for result in report.results),
        "results": [
            {
                "id": result.case.case_id,
                "query": result.query,
                "success": result.success,
                "recall_at_k": result.recall_at_k,
                "mrr": result.mrr,
                "retrieved_paths": list(result.retrieved_paths),
                "missing_paths": list(result.missing_paths),
                "scores": [item.score for item in result.retrieved],
            }
            for result in report.results
        ],
        "by_kind": {
            kind: {
                "count": aggregate.count,
                "recall_at_k": aggregate.recall_at_k,
                "mrr": aggregate.mrr,
                "successes": aggregate.successes,
            }
            for kind, aggregate in report.by_kind.items()
        },
    }


def _json_value(value: object) -> object:
    if value is None:
        return None
    try:
        return normalize_json(value)
    except (TypeError, ValueError):
        return str(value)


def _write_json(value: object, *, stream: Any | None = None, pretty: bool = False) -> None:
    if stream is None:
        stream = sys.stdout
    json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2 if pretty else None, separators=None if pretty else (",", ":"))
    stream.write("\n")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["build_parser", "main"]
