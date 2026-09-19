from __future__ import annotations

import json
from pathlib import Path

import pytest

from kb_retrieval_core.cli import main
from kb_retrieval_core.diagnostics import DIAGNOSTIC_CODES, diagnostic_code
from kb_retrieval_core.context import ContextAssemblyError
from kb_retrieval_core.embeddings import EmbeddingProviderError
from kb_retrieval_core.evaluation import EvaluationError, EvaluationLoadError
from kb_retrieval_core.retrieval import RetrievalError
from kb_retrieval_core.rrf import FusionError
from kb_retrieval_core.snapshot import SnapshotLoadError
from kb_retrieval_core.sqlite_index import SQLiteIndexError
from kb_retrieval_core.vector_retrieval import VectorRetrievalError
from kb_retrieval_core.vector_store import VectorSidecarError


FIXTURE = Path(__file__).parent / "fixtures" / "acceptance"


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (SnapshotLoadError("x"), "snapshot_load_error"),
        (SQLiteIndexError("x"), "sqlite_index_error"),
        (VectorSidecarError("x"), "vector_sidecar_error"),
        (VectorRetrievalError("x"), "vector_retrieval_error"),
        (RetrievalError("x"), "retrieval_error"),
        (FusionError("x"), "fusion_error"),
        (ContextAssemblyError("x"), "context_assembly_error"),
        (EvaluationLoadError("x"), "evaluation_load_error"),
        (EvaluationError("x"), "evaluation_error"),
        (EmbeddingProviderError("x"), "embedding_provider_error"),
    ],
)
def test_package_errors_map_to_their_documented_code(error: Exception, code: str) -> None:
    assert diagnostic_code(error) == code


def test_package_error_code_wins_over_its_builtin_base() -> None:
    # Every package error derives from ValueError or RuntimeError; the generic
    # fallback must never shadow the specific code.
    assert diagnostic_code(ValueError("x")) == "invalid_input"
    assert diagnostic_code(SnapshotLoadError("x")) == "snapshot_load_error"


def test_builtin_errors_map_to_generic_codes() -> None:
    assert diagnostic_code(ValueError("x")) == "invalid_input"
    assert diagnostic_code(TypeError("x")) == "invalid_input"
    assert diagnostic_code(KeyError("x")) == "invalid_input"
    assert diagnostic_code(FileNotFoundError("x")) == "io_error"
    assert diagnostic_code(RuntimeError("x")) == "internal_error"


def test_codes_are_unique_and_stable_strings() -> None:
    codes = set(DIAGNOSTIC_CODES.values())
    assert len(codes) == len(DIAGNOSTIC_CODES)
    assert all(code == code.lower() and code.isidentifier() for code in codes)


def test_diagnostic_code_is_part_of_the_public_surface() -> None:
    import kb_retrieval_core

    assert "diagnostic_code" in kb_retrieval_core.__all__
    assert kb_retrieval_core.diagnostic_code(SnapshotLoadError("x")) == "snapshot_load_error"


def test_cli_error_envelope_carries_the_diagnostic_code(capsys, tmp_path: Path) -> None:
    assert main(["search", "query", "--index", str(tmp_path / "missing")]) == 2
    error = json.loads(capsys.readouterr().err)
    assert error["code"] == "sqlite_index_error"
    assert error["command"] == "search"


def test_cli_error_envelope_reports_snapshot_failures_separately(capsys, tmp_path: Path) -> None:
    assert (
        main(
            [
                "build",
                "--content-root",
                str(tmp_path / "missing"),
                "--graph",
                str(FIXTURE / "graph.json"),
                "--references",
                str(FIXTURE / "references.yml"),
                "--index",
                str(tmp_path / ".retrieval"),
            ]
        )
        == 2
    )
    error = json.loads(capsys.readouterr().err)
    assert error["code"] == "snapshot_load_error"
    assert error["command"] == "build"


def test_cli_argument_errors_report_a_usage_code_without_a_command(capsys) -> None:
    assert main(["search"]) == 2
    error = json.loads(capsys.readouterr().err)
    assert error["code"] == "usage_error"
    assert "command" not in error
