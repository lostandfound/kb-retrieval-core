# Repository instructions

Before changing retrieval behavior, read `docs/architecture.md` completely.
It is the canonical record of the package boundary, input contract, provenance
rules, and implementation sequence.

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

