# Repository instructions

Before changing retrieval behavior, read `docs/ARCHITECTURE.md` completely.
It is the canonical record of the package boundary, input contract, provenance
rules, and compatibility policy.

## Session bootstrap

This file is the handoff entry point for a new development session rooted at
this repository. Start by running `git status --short --branch` and
`git log -5 --oneline`; do not assume the working tree is clean or that the
state below is newer than Git history.

Current project state:

- Milestone 4 is complete and `v0.2.0` is the latest release tag.
- `main` contains post-release correctness fixes for CLI schema migration and
  vector-sidecar cleanup. Treat new work as maintenance or a newly specified
  milestone, not as unfinished Milestone 4 implementation.
- `docs/ISSUES.md` is the implementation ledger and
  `docs/release-audit-2026-09-18.md` records the last completed release gate.
- The deterministic lexical path remains the default. Vector/hybrid retrieval
  stays optional until a consumer-owned admission profile approves it.

Related repositories are siblings under `/Users/lostandfound/lab`:

- `okinawa-karate-book`: consumer KB, retrieval profile, evaluation cases, and
  admission gate.
- `okinawa-karate-rag`: Ollama CLI and answer-generation layer. It owns
  `index`, `retrieve`, and `ask`, structured answers, citation validation, and
  Claim hedging.
- `kb-retrieval-core` (this repository): domain-independent retrieval and
  Evidence Packet assembly only.

Read the sibling repository's own `AGENTS.md` and architecture before changing
it. Do not modify a sibling merely because it is mentioned here; keep changes
within the user's requested scope. When a release or milestone changes the
facts above, update this handoff in the same change.

## Invariants

- Keep this package domain-independent.
- Treat Markdown KB files and their generated graph as source inputs. Indexes
  are disposable derived artifacts and must be reproducible.
- Do not add LLM clients, prompting, chat history, HTTP APIs, authentication,
  or UI code to this package.
- Preserve the path from every result to its entity, section, source IDs, and
  applicable Claim status and confidence.
- Do not import private implementation details from `kb-harness-core`.
- Make vector retrieval optional; deterministic lexical retrieval must work
  without an external service.
- Add or update tests for every behavior change.

## Verification

Run:

```bash
PYTHONPATH=src python3 -m pytest
```
