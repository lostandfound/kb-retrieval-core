import json
from pathlib import Path

from kb_retrieval_core.cli import main
from kb_retrieval_core.vector_store import SQLiteVectorSidecar


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


def test_cli_builds_and_uses_offline_vector_sidecar(tmp_path: Path, capsys, monkeypatch) -> None:
    closed: list[Path] = []
    original_close = SQLiteVectorSidecar.close

    def record_close(sidecar: SQLiteVectorSidecar) -> None:
        closed.append(sidecar.database_path)
        original_close(sidecar)

    monkeypatch.setattr(SQLiteVectorSidecar, "close", record_close)
    index = tmp_path / ".retrieval"
    vectors = tmp_path / ".vectors"
    assert main(_build_args(index)) == 0
    capsys.readouterr()
    assert main([
        "vector-build", "--index", str(index), "--vector-index", str(vectors),
        "--dimension", "4",
    ]) == 0
    built = json.loads(capsys.readouterr().out)
    assert built["test_embedder_only"] is True
    assert built["manifest"]["dimension"] == 4

    assert main([
        "inspect", "/entities/source.md", "--index", str(index),
        "--vector-index", str(vectors),
    ]) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["vector_manifest"]["vector_index_fingerprint"] == built["manifest"]["vector_index_fingerprint"]

    assert main([
        "search", "teaches", "--index", str(index), "--vector-index", str(vectors),
        "--mode", "hybrid", "--top-k", "2",
    ]) == 0
    searched = json.loads(capsys.readouterr().out)
    assert searched["results"]
    assert all(item["retriever"] == "rrf.entity" for item in searched["results"])

    assert main([
        "context", "teaches", "--index", str(index),
        "--vector-index", str(vectors), "--mode", "hybrid", "--top-k", "2",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["packets"]

    assert main([
        "eval", "--index", str(index), "--vector-index", str(vectors),
        "--mode", "hybrid",
    ]) == 0
    evaluated = json.loads(capsys.readouterr().out)
    assert evaluated["embedding_fingerprint"] == built["manifest"]["embedding_fingerprint"]
    assert evaluated["vector_index_fingerprint"] == built["manifest"]["vector_index_fingerprint"]
    assert len(closed) == 5
