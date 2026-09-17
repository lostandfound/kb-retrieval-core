from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from kb_retrieval_core import Entity, SQLiteIndex, Snapshot
from kb_retrieval_core.cli import _evaluation_config, main
from kb_retrieval_core import __version__


FIXTURE = Path(__file__).parent / "fixtures" / "acceptance"


def _build_args(index: Path) -> list[str]:
    return [
        "build",
        "--content-root",
        str(FIXTURE / "content"),
        "--graph",
        str(FIXTURE / "graph.json"),
        "--references",
        str(FIXTURE / "references.yml"),
        "--eval",
        str(FIXTURE / "evals" / "rag-eval.yml"),
        "--index",
        str(index),
    ]


def test_cli_build_search_inspect_and_eval(tmp_path: Path, capsys) -> None:
    index = tmp_path / ".retrieval"
    assert main(_build_args(index)) == 0
    built = json.loads(capsys.readouterr().out)
    assert built["command"] == "build"
    assert built["manifest"]["format"] == "kb-retrieval-sqlite"

    assert main(["search", "teaches", "--index", str(index), "--top-k", "2"]) == 0
    searched = json.loads(capsys.readouterr().out)
    assert searched["results"][0]["entity_path"] == "/entities/source.md"
    assert searched["results"][0]["source_ids"] == ["book"]

    assert main(["inspect", "/entities/source.md", "--index", str(index)]) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["entity"]["title"] == "Source Person"
    assert inspected["chunks"][0]["heading"] == "Biography"

    assert main(["eval", "--index", str(index), "--k", "5"]) == 0
    evaluated = json.loads(capsys.readouterr().out)
    assert evaluated["recall_at_k"] == 1.0
    assert evaluated["mrr"] == 1.0
    assert evaluated["snapshot_hash"] == built["manifest"]["source_hash"]
    assert evaluated["package_identity"] == f"kb-retrieval-core@{__version__}"
    assert evaluated["fusion_config"]["graph"]["enabled"] is False

    assert main(["eval", "--index", str(index), "--k", "5"]) == 0
    repeated = json.loads(capsys.readouterr().out)
    for field in ("snapshot_hash", "evaluation_case_hash", "package_identity", "retrieval_mode", "k", "fusion_config"):
        assert repeated[field] == evaluated[field]

    assert main(["context", "teaches", "--index", str(index), "--top-k", "1"]) == 0
    context = json.loads(capsys.readouterr().out)
    assert context["command"] == "context"
    assert context["packets"][0]["entity_path"] == "/entities/source.md"


def test_cli_serialization_contract_is_stable_for_golden_commands(tmp_path: Path, capsys) -> None:
    index = tmp_path / ".retrieval"
    golden = json.loads((Path(__file__).parent / "fixtures" / "golden" / "cli_contract.json").read_text())
    assert main(_build_args(index)) == 0
    capsys.readouterr()
    assert main(["vector-build", "--index", str(index), "--vector-index", str(tmp_path / ".vectors")]) == 0
    capsys.readouterr()
    commands = [
        ("search", ["search", "teaches", "--index", str(index), "--top-k", "2"]),
        ("context", ["context", "teaches", "--index", str(index), "--top-k", "1"]),
        ("inspect", ["inspect", "/entities/source.md", "--index", str(index)]),
        ("eval", ["eval", "--index", str(index), "--k", "5"]),
        ("hybrid_eval", ["eval", "--index", str(index), "--vector-index", str(tmp_path / ".vectors"), "--mode", "hybrid", "--k", "5"]),
    ]
    for golden_name, command in commands:
        assert main(command) == 0
        first = json.loads(capsys.readouterr().out)
        assert first["schema_version"] == 1
        contract = golden["commands"][golden_name]
        assert set(contract["keys"]).issubset(first)
        for path, expected in contract.get("values", {}).items():
            value = first
            for part in path.split("."):
                value = value[part]
            assert value == expected
        assert main(command) == 0
        second = json.loads(capsys.readouterr().out)
        assert second == first


def test_cli_error_envelope_has_stable_schema_version(capsys, tmp_path: Path) -> None:
    assert main(["search", "query", "--index", str(tmp_path / "missing")]) == 2
    error = json.loads(capsys.readouterr().err)
    assert error == {"schema_version": 1, "error": error["error"]}


def test_cli_graph_expansion_emits_claim_provenance(tmp_path: Path, capsys) -> None:
    index = tmp_path / ".retrieval"
    assert main(_build_args(index)) == 0
    capsys.readouterr()
    assert main([
        "search", "Target Person", "--index", str(index), "--top-k", "5",
        "--expand-graph", "--graph-predicate", "teaches",
    ]) == 0
    searched = json.loads(capsys.readouterr().out)
    graph_results = [item for item in searched["results"] if item["retriever"] == "graph.one-hop"]
    assert graph_results
    assert any(item["metadata"]["claim_path"] == "/claims/teaching.md" for item in graph_results)
    assert all(item["metadata"]["graph_expansion"]["enabled"] is True for item in searched["results"])


def test_evaluation_config_records_effective_graph_cutoffs() -> None:
    args = type("Args", (), {
        "mode": "hybrid",
        "k": 5,
        "expand_graph": True,
        "graph_predicate": ["teaches"],
        "include_rejected_claims": False,
        "include_unknown_claims": True,
    })()
    config = _evaluation_config(args)
    assert config["backend_cutoffs"] == {
        "lexical.entity": 20,
        "lexical.passage": 20,
        "vector": 20,
    }
    assert config["graph"]["effective_seed_cutoff"] == 20
    assert config["graph"]["seed_pool_factor"] == 4
    assert config["graph"]["expanded_result_limit"] == 3
    assert config["graph"]["seed_deduplication"] == "entity_path:first-ranked"
    assert config["rrf"] == {
        "constant": 60.0,
        "backend_weights": {},
        "backend_cutoffs": {
            "lexical.entity": 20,
            "lexical.passage": 20,
            "vector": 20,
        },
        "passage_precedence": [],
    }


def test_context_cli_is_strict_by_default_and_non_strict_is_explicit(tmp_path: Path, capsys) -> None:
    index = tmp_path / ".retrieval"
    SQLiteIndex.build(
        Snapshot(entities=(Entity("/a.md", "Term", "Alpha", source_ids=("missing",), content="# Intro\nAlpha"),)),
        index,
    ).close()

    assert main(["context", "Alpha", "--index", str(index)]) == 2
    strict = json.loads(capsys.readouterr().err)
    assert "missing" in strict["error"]
    assert strict["error"].endswith("'missing'")

    assert main(["context", "Alpha", "--index", str(index), "--non-strict"]) == 0
    non_strict = json.loads(capsys.readouterr().out)
    assert non_strict["unresolved_source_ids"] == ["missing"]
    unresolved = next(item for item in non_strict["packets"][0]["references"] if item["id"] == "missing")
    assert unresolved["resolved"] is False


def test_cli_reports_errors_as_json_and_nonzero(capsys, tmp_path: Path) -> None:
    assert main(["search", "query", "--index", str(tmp_path / "missing")]) == 2
    error = json.loads(capsys.readouterr().err)
    assert "does not exist" in error["error"]


def test_cli_argument_errors_are_json(capsys, tmp_path: Path) -> None:
    assert main(["build"]) == 2
    missing_required = capsys.readouterr()
    assert missing_required.out == ""
    assert "required" in json.loads(missing_required.err)["error"]

    assert main(["search", "query", "--index", str(tmp_path), "--top-k", "invalid"]) == 2
    invalid_integer = capsys.readouterr()
    assert invalid_integer.out == ""
    assert "invalid int value" in json.loads(invalid_integer.err)["error"]


def test_python_module_entrypoint_runs_offline(tmp_path: Path) -> None:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    result = subprocess.run(
        [sys.executable, "-m", "kb_retrieval_core", *_build_args(tmp_path / ".retrieval")],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["command"] == "build"


def test_runtime_version_is_the_build_metadata_source() -> None:
    project = (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert 'dynamic = ["version"]' in project
    assert 'version = {attr = "kb_retrieval_core._version.__version__"}' in project
    assert __version__ == "0.2.0"
