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

## Development

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -e '.[test]'
python3 -m pytest
```

## License

MIT

