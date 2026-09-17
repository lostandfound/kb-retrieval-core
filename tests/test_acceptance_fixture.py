import json
import os
import subprocess
import sys
from pathlib import Path

from kb_retrieval_core import (
    GraphIndex,
    LexicalIndex,
    assemble_context,
    canonical_chunk_bytes,
    canonical_chunk_hash,
    chunk_snapshot,
    evaluate,
    load_evaluation_cases,
    load_snapshot,
)


FIXTURE = Path(__file__).parent / "fixtures" / "acceptance"


def test_acceptance_fixture_load_chunk_search_graph_context_eval_offline() -> None:
    snapshot = load_snapshot(FIXTURE / "content", FIXTURE / "graph.json", FIXTURE / "references.yml", FIXTURE / "evals" / "rag-eval.yml")
    assert {entity.entity_path for entity in snapshot.entities} == {
        "/entities/source.md", "/entities/target.md", "/notes/example.md",
    }
    assert "/index.md" not in {entity.entity_path for entity in snapshot.entities}
    assert {claim.claim_path for claim in snapshot.claims} == {"/claims/teaching.md", "/claims/year.md"}
    assert next(reference for reference in snapshot.references if reference.reference_id == "book").authors == ("Fixture Author",)
    assert next(reference for reference in snapshot.references if reference.reference_id == "book").metadata["checked"] == "2026-01-02"

    chunked = chunk_snapshot(snapshot)
    hits = LexicalIndex(chunked).search("teaches", top_k=3)
    expanded = GraphIndex(chunked).expand(hits, expand=True)
    report = assemble_context(expanded, chunked)
    json.dumps(report.to_dict(), ensure_ascii=False)

    cases = load_evaluation_cases(FIXTURE / "evals" / "rag-eval.yml")
    evaluation = evaluate(cases, lambda query, top_k=5: hits, k=5)
    assert evaluation.results[0].success


def test_acceptance_chunk_bytes_and_hash_are_reproducible() -> None:
    snapshot = load_snapshot(FIXTURE / "content", FIXTURE / "graph.json", FIXTURE / "references.yml")
    first = chunk_snapshot(snapshot).chunks
    second = chunk_snapshot(snapshot).chunks
    assert canonical_chunk_bytes(first) == canonical_chunk_bytes(second)
    assert canonical_chunk_hash(first) == canonical_chunk_hash(second)
    assert canonical_chunk_bytes(first).endswith(b"\n")


def test_package_does_not_import_harness_core() -> None:
    # Use a fresh interpreter: checking this test process's sys.modules can
    # only prove that earlier tests did not import the harness.  The isolated
    # subprocess also makes any accidental network dependency observable.
    script = """
import importlib.abc
import socket
import sys

class BlockHarness(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'kb_harness' or fullname.startswith('kb_harness.'):
            raise ImportError('kb_harness-core is unavailable in this test')
        return None

class BlockNetwork:
    def __call__(self, *args, **kwargs):
        raise AssertionError('package import attempted network access')

sys.meta_path.insert(0, BlockHarness())
socket.socket = BlockNetwork()
import kb_retrieval_core
assert not any(name == 'kb_harness' or name.startswith('kb_harness.') for name in sys.modules)
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[1],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
