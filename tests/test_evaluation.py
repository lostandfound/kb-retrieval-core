from pathlib import Path

import pytest

from kb_retrieval_core import EvaluationCase, EvaluationError, EvaluationLoadError, Evidence, SearchHit, evaluate, load_evaluation_cases


def _hit(path: str, rank: int, score: float = 1.0) -> SearchHit:
    return SearchHit(Evidence(path, f"evidence for {path}"), score, rank, "test")


def test_evaluate_unique_expected_paths_macro_recall_mrr_and_cutoff() -> None:
    cases = (EvaluationCase("one", ("/a.md",), "one", "entity"), EvaluationCase("two", ("/a.md", "/b.md"), "two", "entity"))
    result_map = {"one": (_hit("/a.md", 1),), "two": (_hit("/x.md", 1), _hit("/a.md", 2), _hit("/b.md", 6))}
    report = evaluate(cases, result_map.__getitem__, k=5)
    assert report.recall_at_k == pytest.approx((1 + 0.5) / 2)
    assert report.mrr == pytest.approx((1 + 0.5) / 2)
    assert report.results[1].success is False
    assert report.by_kind["entity"].successes == 1


def test_load_canonical_evaluation_and_preserve_metadata(tmp_path: Path) -> None:
    path = tmp_path / "rag-eval.yml"
    path.write_text("""- id: q1
  query: find source
  expected: A source passage
  evidence: [/source.md]
  kind: entity
  gap: known
  history:
    - date: 2026-01-01
      verdict: OK
  extra: retained
""", encoding="utf-8")
    case = load_evaluation_cases(path)[0]
    assert case.expected == "A source passage"
    assert case.history[0]["verdict"] == "OK"
    assert case.metadata["extra"] == "retained"


def test_load_evaluation_supports_legacy_aliases_and_rejects_duplicate_ids(tmp_path: Path) -> None:
    path = tmp_path / "legacy.yml"
    path.write_text("""cases:
  - id: q1
    question: old query
    expected: old expected answer
    expected_paths: [/source.md]
""", encoding="utf-8")
    assert load_evaluation_cases(path)[0].query == "old query"
    duplicate = tmp_path / "duplicate.yml"
    duplicate.write_text("""- id: q
  query: one
  expected: one
  evidence: [/a.md]
- id: q
  query: two
  expected: two
  evidence: [/b.md]
""", encoding="utf-8")
    with pytest.raises(EvaluationLoadError, match="duplicate case ID"):
        load_evaluation_cases(duplicate)


def test_evaluation_expected_must_be_non_empty_string_and_evidence_is_stably_deduplicated(tmp_path: Path) -> None:
    duplicate_paths = tmp_path / "duplicate-paths.yml"
    duplicate_paths.write_text(
        "- id: q\n  query: one\n  expected: answer summary\n  evidence: [/a.md, /a.md, /b.md]\n",
        encoding="utf-8",
    )
    case = load_evaluation_cases(duplicate_paths)[0]
    assert case.expected_paths == ("/a.md", "/b.md")
    report = evaluate((case,), lambda query, top_k=5: (_hit("/a.md", 1),), k=5)
    assert report.results[0].recall_at_k == 0.5
    assert report.results[0].missing_paths == ("/b.md",)
    assert report.results[0].success is False

    for expected in ("", "   ", 42, False):
        invalid = tmp_path / f"invalid-{repr(expected)}.yml"
        invalid.write_text(f"- id: q\n  query: one\n  expected: {expected!r}\n  evidence: [/a.md]\n", encoding="utf-8")
        with pytest.raises(EvaluationLoadError, match="expected.*non-empty string"):
            load_evaluation_cases(invalid)


@pytest.mark.parametrize("missing", ("id", "query", "expected", "evidence"))
def test_canonical_evaluation_fields_are_required(tmp_path: Path, missing: str) -> None:
    fields = {
        "id": "q1",
        "query": "find source",
        "expected": "source passage",
        "evidence": ["/source.md"],
    }
    del fields[missing]
    path = tmp_path / f"missing-{missing}.yml"
    path.write_text("- " + "\n  ".join(f"{key}: {value!r}" for key, value in fields.items()) + "\n", encoding="utf-8")
    with pytest.raises(EvaluationLoadError, match=missing):
        load_evaluation_cases(path)


def test_evaluate_errors_are_actionable() -> None:
    case = EvaluationCase("query", ("/a.md",), "case-a")
    with pytest.raises(EvaluationError, match="non-SearchHit"):
        evaluate((case,), lambda query: (object(),))
    with pytest.raises(EvaluationError, match="retriever failed"):
        evaluate((case,), lambda query: (_ for _ in ()).throw(RuntimeError("boom")))
