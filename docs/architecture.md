# Architecture and implementation direction

Status: accepted starting design  
Last updated: 2026-09-17

## Purpose

This document is the canonical starting point for future development sessions.
It records the intended package boundary, retrieval design, provenance rules,
and implementation order for `kb-retrieval-core`.

The intended reader is a contributor who understands Python and retrieval
systems but has no access to the conversation that created this repository.

`kb-retrieval-core` is a domain-independent retrieval foundation. It transforms
a validated, structured Markdown knowledge base into ranked,
source-addressable evidence. It is not the application that asks an LLM to
write an answer.

## Context from the first consumer

The initial consumer is the Okinawa karate knowledge base. At the time of the
design discussion it contained 147 graph nodes, 249 edges, and 5 Claims. It
already provided:

- entity Markdown with YAML frontmatter;
- typed `relations`;
- a generated `graph.json`;
- a `references.yml` source registry;
- a distinction between established relations and uncertain Claims;
- `evals/rag-eval.yml` with fixed questions and expected evidence;
- a lexical retrieval smoke test.

These numbers describe the first test corpus, not a package limit or a fixture
that should be hard-coded. At this scale, Neo4j and a dedicated vector database
would add operational cost without solving a demonstrated problem.

## Responsibility boundary

```text
Markdown KB (source of truth)
  |
  +-- kb-ontology-core
  |     Claim, predicate, property, and type constraints
  |
  +-- kb-harness-core
  |     authoring, validation, graph.json generation, and synchronization
  |
  +-- kb-retrieval-core
        snapshot loading
        deterministic chunking
        index construction
        hybrid retrieval
        graph expansion
        evidence assembly
        retrieval evaluation
              |
              v
        consuming RAG application
        LLM, prompts, API, UI, conversation state, and authentication
```

The boundaries are deliberate:

- `kb-ontology-core` owns ontology and Claim validation rules.
- `kb-harness-core` owns creation and validation of the knowledge base.
- `kb-retrieval-core` owns retrieval and evidence assembly over validated
  inputs.
- A consuming application owns answer generation and user-facing behavior.

This package must not depend on private modules from `kb-harness-core`. Its
stable input contract should be files or public interchange objects. If both
packages later need identical frontmatter parsing, extract only that minimal
shared contract after the duplication is demonstrated.

## Source data and derived data

Markdown remains the source of truth. `graph.json`, SQLite indexes, and vector
files are derived artifacts. They must be safe to delete and reproduce.

The initial snapshot loader should accept explicit paths for:

- a content root containing entity Markdown;
- `graph.json`;
- `references.yml`;
- optionally `evals/rag-eval.yml`.

The loader must fail with actionable diagnostics when required input is absent
or structurally invalid. It should not silently repair the source KB; repair is
the harness's responsibility.

## Target package layout

The names below describe responsibilities, not a requirement to create empty
modules before their behavior is implemented.

```text
src/kb_retrieval_core/
  models.py         stable Document, Chunk, SearchHit, and Evidence models
  snapshot.py       Markdown, graph, and reference loading
  chunking.py       deterministic heading-aware chunking
  lexical.py        Japanese-capable lexical retrieval
  embeddings.py     optional embedding and vector adapters
  graph_search.py   relation and Claim-aware neighborhood expansion
  retrieval.py      orchestration, fusion, and reranking
  context.py        evidence packet assembly
  evaluation.py     Recall@k, MRR, and evaluation-file support
  cli.py            standalone command-line interface
```

## Retrieval pipeline

The target pipeline is hybrid:

```text
query
  +-- entity retrieval: title, aliases, description, tags, type --+
  +-- lexical passage retrieval: Japanese n-gram or BM25 ----------+-- fusion
  +-- optional vector retrieval -----------------------------------+     |
                                                                        v
                                                        selective graph expansion
                                                                        |
                                                                        v
                                                                  reranking
                                                                        |
                                                                        v
                                                                evidence packet
```

### Entity retrieval

Entity fields should not have equal weight. Exact or close title and alias
matches should generally outrank body-only matches. Description, tags, and type
provide weaker structured signals.

### Lexical passage retrieval

Chunks are searched separately from entities. Japanese search must not assume
that whitespace tokenization is useful. The first implementation may use
deterministic character n-grams. A morphological tokenizer or BM25 backend can
be introduced behind an interface after evaluation demonstrates the need.

### Vector retrieval

Vector search is optional. The package must remain usable and testable without
network access, an embedding API, or a dedicated vector database. Persist the
embedding model identifier and vector dimension whenever embeddings are built.

### Graph expansion

Graph expansion should normally be shallow, initially one hop. It should be
weighted more strongly for relation-oriented questions such as teacher,
student, lineage, founder, creator, or style membership. Expanding every query
unconditionally would introduce unrelated neighboring entities.

### Fusion and reranking

Reciprocal Rank Fusion is the preferred simple baseline because it combines
ranked lists without assuming comparable backend scores. Any later learned or
model-based reranker must be optional and evaluated against the deterministic
baseline.

## Chunk contract

Do not split only by a fixed character count. Preserve the owning entity and
heading. A target chunk representation is:

```json
{
  "chunk_id": "/people/miyagi-chojun.md#経歴",
  "entity_path": "/people/miyagi-chojun.md",
  "entity_type": "Person",
  "title": "宮城長順",
  "heading": "経歴",
  "text": "...",
  "tags": ["naha-te", "goju-ryu"],
  "source_ids": ["ref: miyagi-1936"],
  "relations": [],
  "content_hash": "..."
}
```

Requirements:

- `chunk_id` must be deterministic for unchanged content and structure.
- `entity_path` is bundle-root-relative and begins with `/`.
- heading boundaries and provenance survive retrieval.
- `content_hash` supports incremental rebuild decisions but does not make an
  index authoritative.
- when a long section needs subchunks, their identifiers must remain stable and
  ordered.

The existing `Evidence` and `SearchHit` classes are an initial public surface,
not the final chunk schema. Extend or replace them deliberately with migration
tests before consumers depend on them.

## Claim handling

Claims must never be flattened into established relations without preserving
their epistemic status.

| Input | Retrieval treatment | Answer constraint passed downstream |
|---|---|---|
| ordinary relation | established graph edge | may be stated normally |
| `accepted` Claim | eligible like a relation, with provenance | retain Claim metadata |
| `proposed` Claim | supplemental evidence | must be hedged |
| `disputed` Claim | supplemental, conflict-aware evidence | must identify uncertainty or conflict |
| `rejected` Claim | excluded by default | never use as affirmative support |

Confidence affects both ranking and the expression strength available to the
consumer. For example, a proposed Claim with confidence `C` cannot support the
unqualified statement “A taught B.” The evidence packet must allow the consumer
to say “a source states that B received instruction from A” instead.

The ontology package remains authoritative for valid statuses and confidence
values. This package consumes them; it does not redefine their state machine.

## Provenance and evidence packets

A result must be traceable beyond the Markdown path:

```text
candidate statement
  -> retrieved chunk
     -> entity
        -> source ID in frontmatter
           -> bibliographic record or URL in references.yml
```

A target evidence packet is:

```json
{
  "statement": "宮城長順は東恩納寛量に師事したとされる",
  "entity_path": "/people/miyagi-chojun.md",
  "section": "経歴",
  "source_ids": ["ref: miyagi-1936"],
  "claim_status": null,
  "confidence": null
}
```

The retrieval layer supplies evidence and constraints; it does not generate the
final prose. A downstream application should be able to enforce:

- do not add facts absent from the evidence;
- do not state proposed or disputed Claims as settled facts;
- expose source identifiers or resolved citations.

## Persistence

The first production-capable backend should use SQLite unless measurement shows
that it is insufficient.

```text
.retrieval/
  index.sqlite
  manifest.json
  embeddings.npy    # present only when vector retrieval is enabled
```

The directory name is `.retrieval`, not `.rag`, because answer generation is
outside this package.

The index is expected to represent:

- entities;
- chunks;
- relations;
- Claims;
- references;
- a lexical index;
- source content hashes;
- embedding model metadata when applicable.

The manifest should identify the index format version and every source input
needed to decide whether a rebuild is required. No runtime mutation of the
index may become knowledge that is absent from the source KB.

## CLI direction

Keep the CLI independent of the `kb` command while the API is evolving. The
provisional executable name is `kb-retrieval`:

```bash
kb-retrieval build
kb-retrieval search "宮城長順の師は誰か"
kb-retrieval context "剛柔流の成立を説明して"
kb-retrieval eval
kb-retrieval inspect /people/miyagi-chojun.md
```

Commands must support machine-readable JSON output and meaningful exit codes.
After the API is stable, `kb rag` or another harness command may delegate to
this executable, but that integration is not part of the core package.

## Evaluation

The Okinawa karate `evals/rag-eval.yml` format is the initial acceptance corpus.
Each entry associates a query with one or more expected evidence paths. The
current harness smoke test is a baseline to supersede, not code to copy without
review.

At minimum report:

- Recall@k, starting with Recall@5;
- MRR;
- per-query retrieved paths and scores on failure;
- aggregate results separated by query kind when the fixture provides it.

Evaluation must work without an LLM. Answer-quality evaluation belongs to the
consuming application, although its results may later inform retrieval tests.

## Implementation sequence

### Milestone 1: deterministic lexical baseline

This is the next implementation target. Do not connect an LLM yet.

1. Define snapshot, entity, chunk, relation, Claim, and reference models.
2. Load a validated KB snapshot without importing harness internals.
3. Implement deterministic heading-aware chunking.
4. Implement Japanese-capable lexical entity and chunk search.
5. Implement selective one-hop relation expansion.
6. Read `evals/rag-eval.yml` and calculate Recall@5 and MRR.
7. Return evidence and resolved source metadata as JSON.

Acceptance criteria:

- rebuilding identical inputs produces byte-equivalent chunk records and index
  contents, excluding explicitly documented timestamps;
- the package runs without network access;
- every hit retains entity, section, and source provenance;
- Claim status and confidence survive loading and retrieval;
- evaluation failures identify the query and retrieved paths;
- all behavior is covered by repository tests.

### Milestone 2: persistent SQLite index

1. Add a versioned SQLite schema and manifest.
2. Persist entity, chunk, graph, Claim, and reference data.
3. Add lexical indexing suitable for Japanese text.
4. Rebuild safely when source or format hashes change.
5. Add `build`, `search`, `inspect`, and `eval` CLI commands.

### Milestone 3: optional vector retrieval

1. Define embedding and vector-store protocols.
2. Add one local or injectable reference implementation.
3. Persist model identity and dimension.
4. Fuse lexical, entity, and vector rankings with RRF.
5. Admit the feature only when evaluation shows value over the lexical
   baseline.

### Milestone 4: retrieval integration contract

1. Stabilize evidence packet serialization.
2. Add the `context` command.
3. Document integration for a separate RAG application.
4. Consider delegation from `kb` only after this contract is stable.

## Deferred decisions

The following choices are intentionally open and should be settled with tests
and measurements rather than preference:

- character n-gram size and weighting;
- SQLite FTS strategy versus a different embedded lexical engine;
- exact RRF constants and graph-expansion weights;
- long-section overlap and subchunk identifiers;
- query classification rules for graph expansion;
- embedding provider and vector persistence format;
- whether a shared public parsing package is justified.

Record resolved decisions in this document or an ADR before implementation
makes them difficult to reverse.

