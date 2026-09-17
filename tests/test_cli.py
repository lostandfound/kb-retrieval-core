from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from kb_retrieval_core.cli import main


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


def test_cli_reports_errors_as_json_and_nonzero(capsys, tmp_path: Path) -> None:
    assert main(["search", "query", "--index", str(tmp_path / "missing")]) == 2
    error = json.loads(capsys.readouterr().err)
    assert "does not exist" in error["error"]


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
