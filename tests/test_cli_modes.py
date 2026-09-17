import json
from pathlib import Path

from kb_retrieval_core.cli import main


FIXTURE = Path(__file__).parent / "fixtures" / "acceptance"


def _build_args(index: Path) -> list[str]:
    return [
        "build", "--content-root", str(FIXTURE / "content"),
        "--graph", str(FIXTURE / "graph.json"),
        "--references", str(FIXTURE / "references.yml"),
        "--eval", str(FIXTURE / "evals" / "rag-eval.yml"),
        "--index", str(index),
    ]


def test_cli_vector_mode_reports_json_error_without_resources(tmp_path: Path, capsys) -> None:
    index = tmp_path / ".retrieval"
    assert main(_build_args(index)) == 0
    capsys.readouterr()
    assert main(["search", "teaches", "--index", str(index), "--mode", "vector"]) == 2
    diagnostic = json.loads(capsys.readouterr().err)
    assert "--vector-index" in diagnostic["error"]
