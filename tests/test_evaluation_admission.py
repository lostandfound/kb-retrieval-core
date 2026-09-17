from kb_retrieval_core import (
    EvaluationCase,
    EvaluationProfile,
    Evidence,
    SearchHit,
    compare_evaluations,
    evaluate,
)


def _report(paths):
    case = EvaluationCase("query", ("/a.md",), "q1", gap="lexical gap")
    hits = tuple(SearchHit(Evidence(path, "text"), 1.0, index, "test") for index, path in enumerate(paths, 1))
    return evaluate((case,), lambda query, top_k=5: hits, k=5, retrieval_mode="hybrid", snapshot_hash="s" * 64, package_identity="commit:test", embedding_fingerprint="e" * 64, vector_index_fingerprint="v" * 64, fusion_config={"constant": 60})


def test_admission_profile_accepts_reproducible_improvement():
    baseline = _report(("/x.md",))
    candidate = _report(("/a.md",))
    comparison = compare_evaluations(baseline, candidate, EvaluationProfile("consumer", minimum_improvement=0.5))
    assert comparison.admitted is True
    assert comparison.primary_improvement == 1.0


def test_admission_profile_rejects_identity_or_regression_failures():
    baseline = _report(("/a.md",))
    candidate = _report(("/x.md",))
    comparison = compare_evaluations(baseline, candidate, EvaluationProfile("consumer"))
    assert comparison.admitted is False
    assert any("below minimum" in item for item in comparison.diagnostics)


def test_admission_requires_gap_improvement_and_vector_identities():
    baseline = _report(("/x.md",))
    candidate = _report(("/a.md",))
    profile = EvaluationProfile("consumer", require_lexical_gap_success=True)
    comparison = compare_evaluations(baseline, candidate, profile)
    assert comparison.admitted is True
    incomplete = evaluate((EvaluationCase("query", ("/a.md",), "q1", gap="gap"),), lambda query, top_k=5: (_ for _ in ()), k=5, retrieval_mode="hybrid", snapshot_hash="s" * 64, package_identity="commit:test")
    rejected = compare_evaluations(baseline, incomplete, profile)
    assert rejected.admitted is False
    assert any("fingerprints" in item for item in rejected.diagnostics)


def test_reproducible_lexical_reports_do_not_require_vector_fingerprints():
    case = EvaluationCase("query", ("/a.md",), "q1")
    kwargs = {
        "k": 5,
        "retrieval_mode": "lexical",
        "snapshot_hash": "s" * 64,
        "package_identity": "kb-retrieval-core@test",
        "fusion_config": {"mode": "lexical", "graph": {"enabled": False}},
    }
    baseline = evaluate((case,), lambda query, top_k=5: (), **kwargs)
    candidate = evaluate(
        (case,),
        lambda query, top_k=5: (SearchHit(Evidence("/a.md", "text"), 1.0, 1, "test"),),
        **kwargs,
    )
    profile = EvaluationProfile("lexical", minimum_improvement=1.0, require_lexical_gap_success=False)
    assert compare_evaluations(baseline, candidate, profile).admitted is True
