# Milestone 4 release audit (2026-09-18)

This record maps the completion gate in `docs/architecture.md` to executable
checks. The temporary `docs/tmp/` audit material is not a release artifact.

## Package checks

| Gate | Evidence |
| --- | --- |
| Effective retrieval, RRF, and graph settings are serialized | `tests/test_cli.py`, including hybrid Golden contract |
| Search/context/inspect/eval JSON compatibility | `tests/fixtures/golden/cli_contract.json` and `test_cli_serialization_contract_is_stable_for_golden_commands` |
| Strict and non-strict provenance | `tests/test_context.py`, `tests/test_cli.py`, `test_rag_integration_contract.py` |
| Domain-neutral offline pipeline | `tests/test_milestone3_acceptance.py` and full suite |
| Public RAG integration surface | `docs/rag-integration.md` and `tests/test_rag_integration_contract.py` |
| Version identity | `__version__ == 0.2.0`, CLI/package identity tests |

Verification on 2026-09-18:

```text
PYTHONPATH=src python3 -m pytest -q
185 passed
git diff --check
clean
```

## Initial consumer gate

The consumer-owned profile is `okinawa-karate-book@f428853`:
`evals/retrieval-admission-profile.yml` and
`tools/check_retrieval_admission.py`.

The real-KB gate produced:

- lexical baseline Recall@5: `0.6138831377961813`
- graph-expanded candidate Recall@5: `0.6719921785139176`
- candidate MRR: `0.8301932367149758`
- q001, q067, q068, q069: all successful
- strict context: no unresolved source IDs and Claim provenance retained
- package identity: `kb-retrieval-core@0.2.0`

## Release decision

The package implementation and the separate RAG integration contract satisfy
the Milestone 4 gate. Consumer vector admission remains governed by the
consumer-owned profile; this package does not enable vector retrieval by
default.
