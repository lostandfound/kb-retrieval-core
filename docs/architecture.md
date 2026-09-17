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

## Canonical interchange schemas

The loader consumes the public interchange produced by `kb-harness-core`; it
does not import harness implementation modules. The following schemas are the
contract for Milestone 1. Additional fields may be retained as metadata, but
the fields below have stable meaning.

### Entity Markdown and YAML frontmatter

Every persisted Markdown document is UTF-8 and has YAML frontmatter delimited
by `---` lines, followed by the Markdown body. A graph-eligible entity has
this persisted shape:

```yaml
type: Person
title: Example Person
description: One-sentence description
tags: [history]
timestamp: 2026-08-25T00:00:00Z
sources: [ref:example-book]
aliases: [Example]
relations:
  - predicate: taught
    target: /people/other.md
    confidence: C       # optional; harness currently emits C only
fields: {}              # optional domain-specific fields
```

The Markdown body supplies sections using ordinary ATX headings, for example
`# Overview` followed by its passage text; there is no persisted `sections`
mapping in frontmatter.

Persisted normal documents require `type`, `title`, `description`, `tags`, and
`timestamp`; `slug` is derived from the filename/path, not persisted
frontmatter, and `sections` are body headings. `aliases`, `relations`, and
domain fields are optional. Whether `sources` is required for an ordinary
document is owned by `vocabulary.yml` (`types.<type>.sources_required`, true by
default) and is enforced by the harness before retrieval. Because the
Milestone 1 loader does not consume the vocabulary, it validates `sources`
when present but must not reject an otherwise valid source-less document.
Claim sources remain unconditionally required by the public Claim contract.
`Index` documents are excluded from retrieval and graph export. Non-Index,
non-graph documents such as `Note` may remain retrievable as passages but need
not appear in `graph.json`.

`relations` entries require non-empty `predicate` and `target`, with an
optional opaque, non-empty confidence value. The current harness validator
allows only `C`; retrieval preserves any value supplied by a future compatible
producer and does not turn an ordinary relation into a Claim.

Claim Markdown carries the same common `title`, `description`, `tags`, and
`timestamp` fields plus `type: Claim`, `subject`, `status`, `confidence`,
`sources`, and exactly one of `predicate` + `object` or `property` + `value`.
The Claim path is retained as provenance. Claims are separate records, not
ordinary persisted graph nodes.

### `graph.json`

The graph exporter writes one object with exactly these top-level collections:

```json
{
  "nodes": [{
    "path": "/people/example.md",
    "type": "Person",
    "title": "Example Person",
    "description": "One-sentence description",
    "tags": ["history"]
  }],
  "edges": [{
    "source": "/people/example.md",
    "predicate": "taught",
    "target": "/people/other.md",
    "confidence": "C"
  }],
  "claims": [{
    "path": "/claims/example.md",
    "subject": "/people/example.md",
    "status": "proposed",
    "confidence": "B",
    "sources": ["ref:archive"],
    "predicate": "taught",
    "object": "/people/other.md"
  }]
}
```

Nodes are a subset of Markdown documents: `Index`, non-graph types, and Claim
documents are not nodes. Claims are a separate `claims` collection, never
ordinary nodes or edges. A Claim uses exactly one form: `predicate` + `object`
for a relation, or `property` + `value` for a value assertion. Graph export
sorts nodes by path, edges by source/predicate/target/confidence, and claims by
Claim path. The graph file does not replace Markdown as the source of
frontmatter, body, or source provenance.

The loader treats `graph.json` as a synchronized derived artifact, not as a
second source to union with Markdown. For every graph node, it compares the
exported `path`, `type`, `title`, `description`, and `tags` with its Markdown
owner. It compares the complete exported edge set with Markdown `relations`
after assigning each relation its owning Markdown path, and compares the
complete graph Claim set with Claim Markdown on all exported Claim fields.
Ordering is irrelevant, but missing, extra, duplicate, or conflicting records
are errors reported as a stale/inconsistent graph. Once consistency is proven,
the snapshot retains exactly one relation or Claim record, enriched with the
owning Markdown provenance; it never silently merges the two representations.
The loader cannot require every ordinary Markdown document to be a graph node,
because graph eligibility is vocabulary-owned and non-graph documents are
valid retrieval inputs.

### `references.yml`

`references.yml` is a YAML mapping from a bare reference ID to a metadata
mapping. Each entry requires non-empty `type` and `title`; a `web` entry also
requires `url`. `author` may be one scalar string and `authors` may be a list
of strings; the loader normalizes either form to an internal `authors` tuple
(`author` becomes a one-item tuple). `lineage`, `pending`, and `checked` are
optional metadata; `checked` is a date normalized to an ISO-8601 JSON date
string. Other bibliographic fields are retained as metadata:

```yaml
archive-1936:
  type: book
  title: A Historical Source
  author: Author
  year: 1936
  checked: 2026-08-25
  pending: optional reason this source is not yet fully checked
web-example:
  type: web
  title: An Online Source
  url: https://example.test/source
```

### `evals/rag-eval.yml`

The canonical evaluation file is a YAML list. Each entry has required fields
`id`, `query`, `expected`, and `evidence`; `history` and `kind` are optional
metadata. `gap` is optional metadata describing a known retrieval gap. The
canonical field set is therefore:

```yaml
- id: q-001
  query: Who taught Example Person?
  expected: A non-empty answer summary or evidence description
  evidence: [/people/example.md, /people/teacher.md]
  kind: relation
  gap: "optional known gap"
  history:
    - date: 2026-01-01
      verdict: OK
```

Unknown evaluation metadata is preserved but does not change scoring. The
harness validates evidence paths against source documents, including
retrievable non-graph documents.

### Source-ID normalization and reference resolution

At the retrieval boundary, source IDs are normalized by trimming whitespace
and converting `ref: ID` to bare `ID`; a bare `ID` remains `ID`. The canonical
internal and serialized `source_ids` representation is always bare IDs. A URL,
citation sentence, or other free text is never parsed into an ID. It remains
the explicit source value: strict context assembly raises an unresolved-source
error, while non-strict assembly retains it as `resolved: false` diagnostic
metadata. Missing IDs are therefore never silently discarded.

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
  "ordinal": 0,
  "entity_type": "Person",
  "title": "宮城長順",
  "heading": "経歴",
  "text": "...",
  "tags": ["naha-te", "goju-ryu"],
  "source_ids": ["miyagi-1936"],
  "relations": [],
  "content_hash": "..."
}
```

Requirements:

- `chunk_id` must be deterministic for unchanged content and structure.
- `entity_path` is bundle-root-relative and begins with `/`.
- `ordinal` is a non-negative, zero-based position in source order within the
  owning entity. It is part of the canonical interchange and is assigned to
  emitted chunks (not omitted whitespace-only sections).
- heading boundaries and provenance survive retrieval.
- `content_hash` supports incremental rebuild decisions but does not make an
  index authoritative.
- when a long section needs subchunks, their identifiers must remain stable and
  ordered.

### Canonical chunk serialization and JSON normalization

The deterministic chunk interchange is JSON Lines (JSONL): one record per
chunk, encoded as UTF-8, with object keys sorted lexicographically and compact
separators `(',', ':')`. Records are emitted in deterministic chunk order
(entity path, then `ordinal`, then chunk identifier), each terminated by
one LF (`\n`), with no timestamps or platform-dependent line endings. The
canonical bytes are exactly the concatenation of those records; a file with
zero records is empty. `content_hash` is SHA-256 of the chunk's normalized
UTF-8 `text` bytes only, before JSON serialization. A snapshot/index hash, when
needed, is SHA-256 of the complete canonical JSONL bytes, not of an in-memory
object representation.

JSON normalization is explicit and shared by serialization and hashing:

- `date` values become ISO-8601 date strings; `datetime` values become
  ISO-8601 strings with UTC normalized to a trailing `Z` (non-UTC values retain
  an explicit numeric offset);
- `pathlib.Path` values become POSIX strings;
- tuples become arrays and sets become arrays sorted by their canonical JSON
  representation;
- mappings have string keys and recursively normalized values;
- finite numbers, booleans, null, and strings remain their JSON equivalents;
- unknown objects, non-finite numbers, and non-string mapping keys are errors,
  never implicit stringification.

This normalization applies to source metadata and context JSON as well as
canonical chunk records.

The existing `Evidence` and `SearchHit` classes are an initial public surface,
not the final chunk schema. Extend or replace them deliberately with migration
tests before consumers depend on them.

## Claim handling

Claims must never be flattened into established relations without preserving
their epistemic status.

Claims are separate source documents and graph records. A Claim has a
root-relative `path` for provenance, a `subject`, one of the two forms
`predicate` + `object` or `property` + `value`, `sources`, a `status`, and an
opaque non-empty confidence value. The ontology package remains authoritative
for valid status and confidence values; retrieval carries them without
numeric validation or redefining the state machine. The public harness
currently validates status as `proposed`, `accepted`, `disputed`, or
`rejected`, and confidence as `A`, `B`, `C`, or `D`.

The deterministic baseline uses this injectable ranking mapping, recorded here
so it can be evaluated and replaced without changing the interchange schema:

```text
A -> 1.00    B -> 0.75    C -> 0.50    D -> 0.25
missing or unknown -> 0.50
```

The mapping is a retrieval ranking policy, not ontology validation. It applies
to Claim confidence and to an ordinary relation's optional confidence when
present. An ordinary relation with no explicit confidence is an established
relation with the default retrieval factor `1.0`; retrieval does not synthesize
a confidence label. The Okinawa profile interprets omission as B-equivalent
for its domain policy, but that interpretation is not written into the
retrieval model. Ordinary relation confidence must remain distinct from Claim status:
an ordinary relation always has `claim_status: null`. `C` or `D` relation and
Claim evidence is eligible for retrieval but requires hedging downstream; it
must not be presented as an unqualified settled fact. The default graph policy
is:

| Input | Retrieval treatment | Downstream constraint |
|---|---|---|
| ordinary relation without confidence | established edge | may be stated normally |
| ordinary relation with confidence | established edge, baseline confidence rank retained | `C`/`D` requires hedging |
| `accepted` Claim | eligible like a relation, with Claim path and source provenance | retain status/confidence |
| `proposed` Claim | supplemental evidence | must be hedged |
| `disputed` Claim | supplemental, conflict-aware evidence | identify uncertainty or conflict |
| `rejected` Claim | excluded by default; explicit opt-in may return it | never use as affirmative support |

Claim and relation provenance must remain source-addressable. A Claim's path
is retained even when its subject/object are graph entities.

## Provenance and evidence packets

A result must be traceable beyond the Markdown path:

```text
candidate statement
  -> retrieved chunk
     -> entity
        -> source ID in frontmatter
           -> bibliographic record or URL in references.yml
```

There are two provenance roles and they must not be conflated:

- passage/entity sources support the retrieved text and come from the owning
  Markdown document or chunk;
- relation/Claim sources support an edge or assertion and come from the
  relation owner or the Claim document (`Claim.sources`).

Graph expansion cites the edge/owner sources for a relation and the Claim
sources for a Claim. It must never substitute the target entity's sources as
support for the relationship. A packet may carry both roles separately so a
consumer can cite the passage and the graph assertion independently.

A target evidence packet is:

```json
{
  "statement": "宮城長順は東恩納寛量に師事したとされる",
  "entity_path": "/people/miyagi-chojun.md",
  "section": "経歴",
  "source_ids": ["miyagi-1936"],
  "passage_source_ids": ["miyagi-1936"],
  "relation_source_ids": [],
  "claim_path": null,
  "claim_status": null,
  "confidence": null
}
```

The packet also preserves retriever, rank, score, text, relation predicate and
direction when applicable, plus resolved reference records. `source_ids` is
the backward-compatible passage/entity role; new consumers should use the
explicit `passage_source_ids` and `relation_source_ids` fields when both roles
are present. Context assembly is strict by default: every ID must resolve in
`references.yml`; non-strict mode emits an explicit unresolved record.
Graph-expanded packets also retain the applied `confidence_weight` in their
metadata. For ordinary relations this is `1.0` when confidence is omitted or
the configured confidence mapping when present; for Claims it is the
configured confidence factor before status decay.

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

The Okinawa karate `evals/rag-eval.yml` format is the initial acceptance
corpus. Each entry has canonical fields `id`, `query`, `expected`, `evidence`,
and optional `history`, `kind`, and `gap`. `expected` is a non-empty string
preserved as descriptive metadata; `evidence` is the list of expected entity paths used for retrieval
scoring. History and all unknown metadata are preserved but do not affect
retrieval metrics.

For a query and direct-retrieval cutoff `k`, deduplicate expected evidence
paths before scoring. Recall@k is the fraction of unique expected paths found
in the first `k` returned entity paths; the reported aggregate is a macro
average over queries (queries with no expected paths are invalid). A query is a
success only when every expected path appears within the first `k`. MRR is the
reciprocal of the rank of the first relevant returned path, or zero if none is
returned. Rank is one-based after the retrieval backend's deterministic cutoff
and before any answer generation. Direct retrieval does not search beyond the
requested `k` to repair a miss.

At minimum report Recall@5, MRR, per-query returned paths/scores on failure,
success, and macro aggregates separated by `kind` when present. Evaluation
must work without an LLM. Answer-quality evaluation belongs to the consuming
application.

## Milestone 1 acceptance fixture and executable checks

The repository acceptance fixture is intentionally small and domain-neutral:

```text
tests/fixtures/acceptance/
  content/
    entities/source.md
    entities/target.md
    claims/teaching.md
    index.md                 # excluded from retrieval and graph
    notes/example.md         # retrievable passage, not a graph node
  graph.json                  # nodes, edges, and separate claims
  references.yml              # at least one resolved and one pending source
  evals/rag-eval.yml          # fields id/query/expected/evidence/kind/gap
```

The fixture must include an ordinary relation with confidence `C`, a Claim
with relation form and `D` confidence, a Claim with value form, a non-graph
document, and source entries using both `ref: ID` and bare `ID` spellings. The
acceptance test is executable offline and follows this exact path:

```text
load Markdown + graph.json + references.yml
  -> normalize source IDs and build Snapshot
  -> deterministic heading-aware chunks
  -> lexical search with a fixed query and cutoff
  -> selective one-hop graph expansion
  -> strict context assembly and JSON serialization
  -> evaluation loading and Recall@5/MRR
```

Required tests include: importing the package with network access disabled and
without `kb-harness-core` on `sys.path`; loading the fixture end to end;
asserting passage versus relation/Claim source roles; asserting strict and
explicit non-strict unresolved-source behavior; serializing context with
`json.dumps`; rebuilding chunks twice and comparing canonical JSONL bytes and
hashes byte-for-byte; and verifying direct-retrieval cutoff and metric
semantics. No test may depend on wall-clock timestamps, filesystem traversal
order, locale, or network access.

## Implementation sequence

### Milestone 1: deterministic lexical baseline

This is the next implementation target. Do not connect an LLM yet.

1. Define snapshot, entity, chunk, relation, Claim, and reference models.
2. Load a validated KB snapshot without importing harness internals.
3. Implement deterministic heading-aware chunking.
4. Implement Japanese-capable lexical entity and chunk search.
5. Implement selective one-hop relation expansion.
6. Read `evals/rag-eval.yml` and calculate Recall@5 and MRR.
7. Return evidence and resolved source metadata as JSON, retaining separate
   passage/entity and relation/Claim source roles.
8. Run the executable acceptance fixture and deterministic serialization checks.

Acceptance criteria:

- rebuilding identical inputs produces byte-equivalent canonical UTF-8 JSONL
  chunk records and hashes, with no timestamps;
- the package runs without network access;
- every hit retains entity, section, passage sources, and applicable relation or
  Claim sources;
- Claim status and opaque A/B/C/D-style confidence survive loading and
  retrieval without retrieval redefining ontology validity;
- ordinary relation confidence is retained and ranked while `claim_status`
  remains null;
- source IDs normalize to bare IDs and strict mode never silently drops an
  unresolved URL or free-text source;
- evaluation failures identify the query and retrieved paths, and metrics obey
  unique-path macro Recall@k, first-relevant MRR, and direct cutoffs;
- all behavior is covered by repository tests.

### Milestone 2: persistent SQLite index

1. Add a versioned SQLite schema and manifest.
2. Persist entity, chunk, graph, Claim, and reference data.
3. Add lexical indexing suitable for Japanese text.
4. Rebuild safely when source or format hashes change.
5. Add `build`, `search`, `inspect`, and `eval` CLI commands.

Milestone 2 is accepted only when executable offline tests verify all of the
following:

- the manifest and SQLite database carry explicit format and schema versions,
  and opening an unsupported or mutually inconsistent version fails with an
  actionable error;
- entity, deterministic chunk, relation, both Claim forms, reference, and
  provenance fields survive a build/open round trip after canonical JSON
  normalization;
- reopened SQLite indexes return the same deterministic lexical results as the
  freshly built index, including a Japanese query that does not rely on word
  boundaries or whitespace;
- identical source inputs produce identical manifests, while a source-content
  or chunk-hash change is detected by `needs_rebuild`;
- rebuilding replaces stale derived rows, and a failure before replacement
  leaves the previously completed database readable;
- the sidecar manifest is reproducible from the manifest stored in SQLite;
  disagreement between them is rejected rather than silently selecting one;
- `build`, `search`, `inspect`, and `eval` each run against the acceptance
  fixture, emit JSON, require no network or LLM, and return a non-zero status
  with a JSON diagnostic for invalid input.

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

## Decisions made for Milestone 1

The following are now contract decisions, not deferred preferences:

- canonical interchange is the harness-compatible Markdown/frontmatter,
  `graph.json`, `references.yml`, and `evals/rag-eval.yml` described above;
- Claims are separate from entities and support both relation and value forms,
  with Claim path provenance;
- confidence is opaque at the model boundary, with the injectable A/B/C/D
  baseline mapping documented above;
- ordinary relation confidence is retained separately from Claim status;
- source IDs normalize to bare IDs, with strict unresolved-source behavior;
- passage/entity and relation/Claim provenance roles remain distinct;
- JSON normalization and canonical chunk JSONL bytes are defined above;
- the offline acceptance fixture and end-to-end test path are mandatory.

The following remain intentionally open and should be settled with tests and
measurements rather than preference:

- character n-gram size and weighting;
- SQLite FTS strategy versus a different embedded lexical engine;
- exact RRF constants and graph-expansion weights beyond the confidence
  baseline;
- long-section overlap and future subchunk identifiers;
- query classification rules for graph expansion;
- embedding provider and vector persistence format;
- whether a shared public parsing package is justified;
- the eventual stable CLI flags and persistent-index manifest details.

Record any resolution in this document or an ADR before implementation makes
it difficult to reverse.
