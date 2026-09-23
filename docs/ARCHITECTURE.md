# Architecture and package contract

Status: current package contract; Milestone 4 is complete.
Last updated: 2026-09-24

## Purpose

This document is the canonical record of the package boundary, input and
output contracts, compatibility rules, provenance requirements, and vector
admission policy for `kb-retrieval-core`. The completed implementation history
and its checks are recorded in [`ISSUES.md`](ISSUES.md) and the
[Milestone 4 release audit](release-audit-2026-09-18.md).

The intended reader is a contributor who understands Python and retrieval
systems and needs the package contract without relying on chat history.

`kb-retrieval-core` is a domain-independent retrieval foundation. It transforms
a validated, structured Markdown knowledge base into ranked,
source-addressable evidence. An Evidence Packet is the serializable retrieval
result with its text and provenance. This package does not generate answers.

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
  |     Claim (a separately sourced assertion with status and confidence),
  |     predicate, property, and type constraints
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

Markdown remains the source of truth. `graph.json`, SQLite indexes, and the
separate SQLite vector sidecar (a disposable database for vectors) are derived
artifacts. They must be safe to delete and reproduce.

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
package input contract. Additional fields may be retained as metadata, but the
fields below have stable meaning.

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
loader does not consume the vocabulary, it validates `sources`
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

Evaluation evidence paths may name either ordinary document/entity paths or
Claim document paths. A returned hit satisfies its entity path and, when graph
or assertion metadata carries one, its `claim_path`. The rank of the owning
hit is used for either path. This keeps Claims out of the entity index while
making their required provenance observable and scoreable. Value Claims remain
separate assertion records. Opt-in graph expansion emits a value Claim as a
subject-bound assertion hit (direction `assertion`) without inventing a graph
node or relationship to its scalar value, allowing its path to satisfy an
evaluation case.

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

## Retrieval pipeline

The retrieval pipeline supports lexical search and optional hybrid retrieval:

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
whitespace tokenization is useful. The current implementation uses
deterministic character n-grams. A different lexical method requires evidence
from evaluation and must preserve deterministic offline retrieval.

### Vector retrieval

Vector search is optional. The package must remain usable and testable without
network access, an embedding API, or an embedding dependency. Package-owned
`embed_documents` and `embed_query` operations return finite fixed-dimension
vectors without exposing provider response objects. Document and query task
settings are recorded separately for asymmetric models. `embed_documents`
receives ordered inputs and returns exactly one vector per input in the same
order; duplicate IDs, missing or extra results, and reordered results are
rejected. The canonical configuration records provider or implementation,
model and revision, tokenizer or preprocessing, pooling, normalization, and
dimension;
its canonical JSON hash is the embedding fingerprint. A separate vector-index
fingerprint also includes similarity metric and vector-format version. The
lexical package must not initialize an embedding implementation or load model
files during import or lexical retrieval.

The local vector store is a disposable, versioned SQLite sidecar keyed to the
lexical snapshot and chunk hash. It stores chunk IDs and content hashes,
vectors, embedding and vector-index fingerprints, metric, dimension, and
format version. Its manifest must agree with the manifest stored in SQLite;
incompatible identities require rebuilding. Atomic replacement and sidecar
deletion or corruption must leave lexical retrieval usable. A deterministic
test embedder and canonical configuration produce byte-equivalent vector
artifacts and identical rankings for identical ordered inputs. External vector
adapters expose the same manifest semantics.

### Graph expansion

Graph expansion should normally be shallow, initially one hop. It should be
weighted more strongly for relation-oriented questions such as teacher,
student, lineage, founder, creator, or style membership. Expanding every query
unconditionally would introduce unrelated neighboring entities.

The first orchestration contract therefore exposes graph expansion as an
explicit, default-disabled retrieval option. When enabled, expansion runs
after direct backend retrieval or fusion and before the final cutoff. Result
metadata and evaluation configuration record the applied graph policy. A
future query classifier may select this option, but classification remains
consumer policy until an evaluated domain-independent rule exists.

When expansion is enabled, orchestration retrieves a deterministic seed pool
larger than the final cutoff (default factor `4`), collapses direct passage
hits to one representative per entity, and expands those entity seeds. The
final merge reserves a bounded portion of the cutoff (default half rounded up)
for graph evidence. It first retains the highest-scoring ordinary relation,
then distinct Claim paths, then further ordinary relations if capacity remains;
the rest is filled by entity-deduplicated direct results. Scores remain
unchanged and determine display order after selection. The seed factor, seed
count, and graph-result limit are serialized in result metadata.

Within the reserved ordinary-relation candidates, neighbors with the same
entity type as their seed are selected before cross-type neighbors, then by
score and stable path order. This structural heuristic favors person-to-person
and organization-to-organization relationship evidence without interpreting
domain predicate names or query language.

### Fusion and reranking

Reciprocal Rank Fusion combines rankings without assuming comparable backend
scores. Before fusion, passage and vector lists contribute at most one
candidate per entity, using that entity's highest-ranked chunk. Entity-only
hits contribute their entity rank; when no passage backend contributes, their
evidence falls back to the entity description or lowest-ordinal chunk. The
selected evidence retains section, chunk ID, text, and passage sources using a
documented backend precedence. Entity path breaks final ties. RRF constant,
backend weights and cutoffs, and passage precedence are serialized as fusion
configuration. Any learned or model-based reranker remains optional and is
evaluated against the deterministic baseline.

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

A context packet is the JSON evidence object assembled for a consumer:

```json
{
  "text": "宮城長順は東恩納寛量に師事したとされる",
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

The version 2 lexical index uses SQLite. It stores entities, chunks, relations, Claims,
references, lexical postings, and source-content hashes. Character n-gram
postings are persisted and reopened searches retrieve candidates from those
postings before deterministic scoring; SQLite is not merely a serialized
snapshot cache.

```text
.retrieval/
  index.sqlite
  manifest.json
.retrieval-vectors/             # optional, disposable sidecar
  vector.sqlite
  vector-manifest.json
```

The lexical and vector indexes are separate derived artifacts. Unsupported schema
versions and mutually inconsistent manifests fail with actionable errors. Identical
source inputs produce identical manifests; source or chunk-hash changes are
detected by `needs_rebuild`. Rebuilding replaces stale derived records, and a
failed rebuild leaves the last completed database readable. Build/open
round-trips preserve normalized entity, chunk, relation, both Claim forms,
reference, and provenance data. Reopened SQLite search returns the same deterministic results
as a freshly built index, including Japanese queries that do not rely on word
boundaries or whitespace. No runtime mutation may add knowledge absent from the
source KB.

## CLI boundary

The CLI remains independent of the `kb` command. Supported commands, flags,
JSON output, and exit behavior are listed in the [README](../README.md).
`vector-build` uses the deterministic offline test embedder for mechanics
checks only. Production and consumer-admission embeddings remain injectable
through the Python API.

## Evaluation

The Okinawa karate `evals/rag-eval.yml` format is the initial acceptance
corpus. Each entry has canonical fields `id`, `query`, `expected`, `evidence`,
and optional `history`, `kind`, and `gap`. `expected` is a non-empty string
preserved as descriptive metadata; `evidence` is the list of expected document
paths used for retrieval scoring. A path may be an entity/document path or a
Claim path under the evidence-path contract above. History and all unknown
metadata are preserved but do not affect retrieval metrics.

For a query and direct-retrieval cutoff `k`, deduplicate expected evidence
paths before scoring. Recall@k is the fraction of unique expected paths found
among the evidence paths attached to the first `k` ranked hits. A hit supplies
its entity path and, when applicable, its Claim path; both are scored at the
owning hit's rank. The reported aggregate is a macro
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

## Verification and implementation record

Milestones 1–4 are complete. Their implementation tasks and acceptance checks
are recorded in [`ISSUES.md`](ISSUES.md); the release audit maps the final
gate to its tests and records the verification results. Keep these records as
the history of completed work rather than as a future implementation plan.

The offline regression suite protects the package contracts above. It exercises
a domain-neutral KB from snapshot loading through deterministic chunking,
lexical retrieval, graph expansion, strict context serialization, and
Recall@k/MRR evaluation. It checks imports without network access or
`kb-harness-core`, distinct passage and assertion provenance, strict and
non-strict unresolved sources, canonical chunk bytes and hashes, and direct
retrieval cutoff semantics. Tests must not depend on wall-clock timestamps,
filesystem traversal order, locale, or network access.

Vector checks cover offline embedding protocol validation, including ordered
input/cardinality and duplicate, missing, extra, or reordered result rejection;
stable fingerprints and byte-equivalent artifacts; sidecar round trips and
incompatibility detection; failed rebuild and deletion isolation from lexical
retrieval; source provenance through vector and fused hits; and deterministic
cutoff and fusion behavior. CLI checks cover
explicit default-disabled vector use, serialized effective settings, and stable
JSON diagnostics. The [release audit](release-audit-2026-09-18.md) identifies
the named tests for these checks and the consumer regression profile.

## Public integration contract

### Supported surface

Supported Python APIs are the names exported from `kb_retrieval_core.__all__`
and listed in the README. The supported CLI commands and flags are documented
in the [README](../README.md). CLI success and error envelopes carry
`schema_version: 1`. Every error envelope also carries a stable `code` from
`kb_retrieval_core.diagnostics` and, once known, the failed `command`. Consumers
classify failures by `code`; the human-readable `error` wording is not a
contract. A minor release may add a code, but an existing code never changes
meaning.

Stable serialized artifacts are lexical and vector manifests, search evidence,
context packets, and evaluation reports. Private helpers, SQLite table layout,
and test embedders are not application contracts.

### Reproducibility

An evaluation report describes the effective run, not only requested flags. It
records package and artifact versions; snapshot, chunk, case, embedding, and
vector identities; retrieval mode and final cutoff; effective backend cutoffs
after seed expansion; complete RRF settings; and the complete graph policy.
The graph policy includes predicate and Claim filters, decay and confidence
weights, seed-pool factor and resulting cutoff, entity deduplication,
graph-result reservation, and structural selection policy. Any field that can
change candidates, ordering, cutoff membership, provenance, or scoring is
compatibility-relevant.

### Compatibility

- Additive optional JSON fields may be introduced in a minor release.
- Removing or renaming a public field, changing its meaning or default, or
  changing rank/cutoff behavior requires a major release or versioned format
  migration.
- Unsupported artifact schemas fail explicitly; indexes remain disposable.
- Default lexical behavior remains offline and deterministic.
- Consumer-specific query policy and vector admission remain outside the
  stable core contract.

### Separate RAG application

The RAG application owns user/query state, prompting, LLM calls, answer
generation, citation presentation, HTTP APIs, authentication, and UI. It opens
or builds an index, selects an explicit retrieval configuration, calls
`search`/`context`, enforces Claim hedging and citation policy, and passes only
assembled evidence to answer generation. See the [RAG integration guide](rag-integration.md)
for the public integration flow.

## Vector admission policy

Vector implementation completion and consumer admission are separate gates.
Passing package-level mechanics checks may ship vector retrieval as
experimental and default-disabled; it does not establish improved retrieval
quality. Each consumer records an evaluation profile fixing separate
development and admission case sets, primary metric, minimum meaningful
improvement, secondary-metric regression tolerance, cutoff, and permitted
per-query regressions before tuning a hybrid candidate. Baseline and candidate
use the same admission cases, snapshot, and cutoff. Synthetic fixtures test mechanics only and never count
for admission.

A consumer may enable hybrid retrieval by default only when a reproducible
admission report shows that:

- the predeclared primary metric improves by at least its minimum;
- each secondary metric stays within its regression tolerance;
- at least one documented lexical-gap case becomes successful;
- general regression cases stay within the per-query allowance; and
- the report includes all required run identities and retrieval configuration.

The admission set includes documented lexical gaps, including a Japanese
paraphrase or synonym whose expected evidence cannot be recovered by direct
character overlap alone. If that gap is absent, a threshold fails, or the report
is not reproducible, vector retrieval remains experimental and
default-disabled. Baseline and candidate reports are derived evaluation
artifacts, not knowledge-base source data.

## Design extension conditions

Tune n-gram weighting or RRF parameters only when measured retrieval results
show a need. Add long-section overlap only with stable subchunk identifiers.
Query classification remains consumer policy until a domain-independent rule
has evaluation evidence. Extract shared parsing code only after both packages
show actual duplication.
