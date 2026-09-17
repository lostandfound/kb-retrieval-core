# kb-retrieval-core

Domain-independent retrieval primitives for structured Markdown knowledge bases.

`kb-retrieval-core` turns a validated knowledge-base snapshot into ranked,
source-addressable evidence. It does not own the knowledge base, call an LLM,
or generate answers.

## Scope

The package is intended to provide:

- deterministic entity and heading-aware chunking;
- lexical, vector, and graph retrieval adapters;
- hybrid ranking;
- source and claim provenance preservation;
- retrieval evaluation such as Recall@k and MRR;
- backend-independent evidence packets for downstream applications.

It deliberately excludes:

- knowledge-base authoring and validation;
- ontology and claim-state rules;
- prompts, model clients, chat history, APIs, and user interfaces;
- databases as a source of truth.

Markdown and its generated graph remain the source inputs. Search indexes are
disposable artifacts that must be reproducible from those inputs.

## Package relationship

```text
kb-ontology-core   ontology and claim rules
kb-harness-core    KB authoring, validation, and derived artifacts
kb-retrieval-core  indexing, retrieval, and evidence assembly
RAG application    answer generation, API, UI, and conversation state
```

The initial public API contains only stable value objects. Retrieval backends
will be added behind explicit protocols so applications do not depend on a
particular vector database or embedding provider.

## Design and roadmap

Read [Architecture and implementation direction](docs/architecture.md) before
adding retrieval behavior. It defines the package boundary, input and evidence
contracts, Claim handling, retrieval pipeline, and staged implementation plan.

## Development

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -e '.[test]'
python3 -m pytest
```

## CLI

The package provides an offline `kb-retrieval` command (also available as
`python3 -m kb_retrieval_core`). Build a disposable SQLite index from a KB
snapshot, then search or inspect it:

```bash
kb-retrieval build \
  --content-root tests/fixtures/acceptance/content \
  --graph tests/fixtures/acceptance/graph.json \
  --references tests/fixtures/acceptance/references.yml \
  --eval tests/fixtures/acceptance/evals/rag-eval.yml \
  --index .retrieval
kb-retrieval search "Source Person teaches Target Person" --index .retrieval
kb-retrieval inspect /entities/source.md --index .retrieval
kb-retrieval eval --index .retrieval
```

All commands emit JSON and run without an LLM, network access, or a separate
database service.

## License

MIT
