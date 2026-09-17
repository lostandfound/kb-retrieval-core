# Issue list

Project: `kb-retrieval-core` Milestone 3 — optional vector retrieval  
Created: 2026-09-17  
Canonical requirements: `docs/architecture.md`, “Milestone 3: optional vector
retrieval”

These are implementation issues, not yet-created GitHub issue numbers. Each
issue is intended to fit within one to three working days and must preserve the
domain-independent, offline lexical baseline. The consumer admission gate is
deliberately separate from repository implementation acceptance.

## Dependency order

```text
#1 embedding contracts and fingerprints
 ├── #2 injectable reference embedder
 ├── #3 SQLite vector sidecar
 └── #7 reproducible evaluation metadata

#2 + #3 ──> #4 vector-only retrieval
#1 ───────> #5 entity-level RRF
#4 + #5 ──> #6 hybrid retrieval orchestration
#3 + #4 + #6 + #7 ──> #8 vector CLI
#1–#8 ──> #9 offline Milestone 3 acceptance
```

Issues #2, #3, #5, and the non-vector portions of #7 may proceed in parallel
after #1. Every issue includes its own unit tests; #9 adds the executable
cross-component acceptance path.

## Issue #1: Define embedding contracts and canonical fingerprints

**Title**: `feat: define embedding contracts and fingerprints`  
**GitHub Issue**: not created  
**Status**: completed in `5dd0d66`
**Purpose**: Establish provider-independent document/query embedding boundaries
and reproducible compatibility identities before any backend is implemented.

**Implementation**:

- Add package-owned embedding configuration and protocol types, likely in
  `src/kb_retrieval_core/embeddings.py`.
- Keep `embed_documents` ordered and distinct from `embed_query`.
- Validate finite numeric values, fixed dimensions, exact document-result
  cardinality, and result order.
- Canonically serialize provider/implementation identity, model and revision,
  task settings, tokenizer/preprocessing identity, pooling, normalization, and
  dimension.
- Derive `embedding_fingerprint`; derive a vector-index fingerprint by adding
  similarity metric and vector-format version.
- Export only stable public types from `kb_retrieval_core`.

**DoD**:

- [x] Document and query embedding paths have separate protocol methods.
- [x] Canonically equivalent configurations produce identical fingerprints.
- [x] Any compatibility-relevant configuration change changes the fingerprint.
- [x] Tests reject NaN/infinity, inconsistent dimensions, missing/extra
      results, non-numeric values, and reordered document results.
- [x] Importing the package requires no embedding SDK, model, or network.
- [x] `PYTHONPATH=src python3 -m pytest` passes.

**Branch**: `feat/embedding-contracts`  
**Dependencies**: none  
**Labels**: `feat`, `priority: high`

## Issue #2: Add an injectable reference embedder

**Title**: `feat: add injectable reference embedder`  
**GitHub Issue**: not created  
**Status**: completed in `00272fc`
**Purpose**: Provide one usable reference implementation and a deterministic
offline implementation for all subsequent tests without selecting a hosted
provider.

**Implementation**:

- Add an adapter accepting explicitly injected document and query embedding
  callables behind the Issue #1 protocol.
- Add a deterministic test embedder with fixed, documented semantics for
  mechanics tests; keep it clearly unsuitable for admission claims.
- Validate adapter outputs through the same contract as any future provider.
- Ensure construction is side-effect free and model initialization remains the
  caller's responsibility.

**DoD**:

- [x] Injected document and query callables are exercised independently.
- [x] The deterministic embedder returns byte-stable vectors for identical
      ordered input and configuration.
- [x] Provider exceptions retain an actionable causal diagnostic.
- [x] No optional dependency is imported by the lexical-only path.
- [x] Unit tests run with network access unavailable.
- [x] `PYTHONPATH=src python3 -m pytest` passes.

**Branch**: `feat/reference-embedder`  
**Dependencies**: Issue #1  
**Labels**: `feat`, `priority: high`

## Issue #3: Implement the versioned SQLite vector sidecar

**Title**: `feat: add persistent SQLite vector sidecar`  
**GitHub Issue**: not created  
**Status**: implemented locally; pending review and commit
**Purpose**: Persist disposable vectors independently from the lexical SQLite
index while detecting every incompatible or stale artifact.

**Implementation**:

- Add a separate vector-sidecar module and versioned SQLite schema.
- Persist lexical snapshot hash, canonical chunk hash, chunk IDs, per-chunk
  content hashes, vectors, embedding and vector-index fingerprints, metric,
  dimension, format version, and counts.
- Build into temporary database/manifest artifacts and atomically install the
  completed pair with rollback behavior equivalent to the lexical index.
- Expose explicit `matches`, `needs_rebuild`, open, close, and deletion-safe
  behavior without importing private lexical-index details.
- Validate schema, manifests, counts, vector dimensions, uniqueness, and
  lexical-index identity when reopening.

**DoD**:

- [x] Build/open round trips preserve all vector values and identity fields.
- [x] Identical inputs produce byte-equivalent canonical vector artifacts.
- [x] Snapshot, chunk, fingerprint, metric, dimension, and format mismatches are
      explicit rebuild conditions.
- [x] Corruption and partial counts fail with actionable diagnostics.
- [x] A failed rebuild preserves the previous vector sidecar.
- [x] Missing, deleted, or corrupt vector sidecars do not damage lexical search.
- [x] `PYTHONPATH=src python3 -m pytest` passes.

**Branch**: `feat/sqlite-vector-sidecar`  
**Dependencies**: Issue #1  
**Labels**: `feat`, `priority: high`

## Issue #4: Implement deterministic vector-only retrieval

**Title**: `feat: implement deterministic vector retrieval`  
**GitHub Issue**: not created  
**Purpose**: Prove embedding and sidecar behavior independently before hybrid
fusion can obscure vector-search defects.

**Implementation**:

- Embed queries through `embed_query` and search the sidecar using its declared
  similarity metric.
- Validate the query fingerprint and dimension before searching.
- Return backend-independent `SearchHit`/`Evidence` values with entity path,
  section, chunk ID, text, content hash, and passage source IDs.
- Define deterministic cutoff, descending score order, and chunk-ID tie-breaks.
- Reject invalid queries, non-positive cutoffs, incompatible sidecars, and
  non-finite similarity results.

**DoD**:

- [ ] Known vectors return the expected ranks and scores for each supported
      metric.
- [ ] Equal scores and cutoff boundaries are deterministic.
- [ ] Reopened sidecars return the same results as freshly built sidecars.
- [ ] Provenance equals the owning chunk provenance and never target-entity
      provenance.
- [ ] Vector-only tests use no network or external service.
- [ ] `PYTHONPATH=src python3 -m pytest` passes.

**Branch**: `feat/vector-retrieval`  
**Dependencies**: Issues #2 and #3  
**Labels**: `feat`, `priority: high`

## Issue #5: Implement entity-level Reciprocal Rank Fusion

**Title**: `feat: implement deterministic entity-level RRF`  
**GitHub Issue**: not created  
**Purpose**: Combine differently scaled rankings without losing deterministic
entity identity or passage provenance.

**Implementation**:

- Add backend-agnostic fusion over ranked `SearchHit` sequences.
- Collapse each backend to its highest-ranked hit per `entity_path`.
- Implement configurable RRF constant, backend weights, backend cutoffs, and
  passage precedence.
- Retain the selected contributing passage; for entity-only results, fall back
  to entity description or lowest-ordinal chunk as specified by architecture.
- Use `entity_path` as the final deterministic tie-breaker and expose complete
  fusion configuration in result metadata.

**DoD**:

- [ ] Tests cover several chunks from one entity and duplicate backend hits.
- [ ] Equal fused scores sort by `entity_path`.
- [ ] Empty optional inputs and different backend score scales behave
      deterministically.
- [ ] Entity-only fallback and passage precedence are directly tested.
- [ ] Selected evidence retains section, chunk ID, text, and source IDs.
- [ ] `PYTHONPATH=src python3 -m pytest` passes.

**Branch**: `feat/entity-rrf`  
**Dependencies**: Issue #1  
**Labels**: `feat`, `priority: high`

## Issue #6: Add optional hybrid retrieval orchestration

**Title**: `feat: add optional hybrid retrieval orchestration`  
**GitHub Issue**: not created  
**Purpose**: Provide one explicit orchestration surface while keeping lexical
retrieval fully functional and default when vector dependencies are absent.

**Implementation**:

- Add retrieval-mode configuration for lexical-only, vector-only, and hybrid
  operation.
- Orchestrate lexical passage, lexical entity, optional vector, and RRF stages
  with explicit per-backend cutoffs.
- Keep vector use opt-in and default-disabled.
- Preserve passage, relation, and Claim provenance roles through fused results.
- Return actionable errors for requested but unavailable or incompatible vector
  resources; never silently change a requested retrieval mode.

**DoD**:

- [ ] Existing lexical behavior and rankings remain unchanged by default.
- [ ] Hybrid mode invokes all configured backends exactly once.
- [ ] Missing optional vector resources affect only modes that request them.
- [ ] Fused evidence passes existing context/provenance invariants.
- [ ] Retrieval mode and complete fusion configuration are serialized.
- [ ] `PYTHONPATH=src python3 -m pytest` passes.

**Branch**: `feat/hybrid-retrieval`  
**Dependencies**: Issues #4 and #5  
**Labels**: `feat`, `priority: high`

## Issue #7: Make evaluation comparisons reproducible

**Title**: `feat: add reproducible hybrid evaluation profiles`  
**GitHub Issue**: not created  
**Purpose**: Separate repository mechanics from consumer admission and prevent
post-hoc threshold changes.

**Implementation**:

- Extend evaluation reports with retrieval mode, snapshot hash, canonical
  evaluation-case hash, cutoff, package commit/release identity, applicable
  fingerprints, and fusion configuration.
- Define a consumer-owned evaluation profile containing distinct development
  and admission case sets, primary metric, minimum improvement, secondary
  regression tolerances, cutoff, and permitted per-query regressions.
- Add deterministic comparison logic that reports each gate independently;
  comparison does not enable vector retrieval or mutate configuration.
- Document that synthetic fixtures prove mechanics but cannot satisfy consumer
  admission.

**DoD**:

- [ ] Equivalent evaluation files produce the same canonical case hash.
- [ ] Missing identity/configuration fields make a report non-reproducible.
- [ ] Baseline and candidate with different cases, snapshots, or cutoffs cannot
      be compared as an admission pair.
- [ ] Primary, secondary, lexical-gap, and per-query regression gates have
      independent pass/fail diagnostics.
- [ ] Existing lexical evaluation output remains source-compatible or has an
      explicit migration test.
- [ ] `PYTHONPATH=src python3 -m pytest` passes.

**Branch**: `feat/hybrid-evaluation`  
**Dependencies**: Issue #1; final hybrid report integration depends on Issue #6  
**Labels**: `feat`, `priority: medium`

## Issue #8: Expose explicit vector and hybrid CLI workflows

**Title**: `feat: add vector and hybrid CLI workflows`  
**GitHub Issue**: not created  
**Purpose**: Make optional vector behavior inspectable and scriptable without
changing existing lexical command defaults.

**Implementation**:

- Extend `build`, `search`, `inspect`, and `eval` only where needed to support
  explicit vector-sidecar and retrieval-mode options.
- Keep all current invocations lexical-only unless the user opts in.
- Emit fingerprints, metric, dimension, retrieval mode, and fusion
  configuration in JSON where applicable.
- Return non-zero JSON diagnostics for unavailable models, missing or
  incompatible sidecars, invalid dimensions, invalid metrics, and invalid
  fusion parameters.
- Update README examples without presenting experimental vector retrieval as
  admitted for any consumer.

**DoD**:

- [ ] Existing lexical CLI tests remain unchanged and pass.
- [ ] Vector build/search/inspect/eval execute offline with the deterministic
      embedder and acceptance fixture.
- [ ] Every argparse and runtime failure path emits JSON on stderr.
- [ ] `python -m kb_retrieval_core` and installed console-script paths agree.
- [ ] Help text labels vector retrieval optional and default-disabled.
- [ ] `PYTHONPATH=src python3 -m pytest` passes.

**Branch**: `feat/vector-cli`  
**Dependencies**: Issues #3, #4, #6, and #7  
**Labels**: `feat`, `priority: medium`

## Issue #9: Add the executable Milestone 3 acceptance path

**Title**: `test: add milestone 3 vector acceptance coverage`  
**GitHub Issue**: not created  
**Purpose**: Demonstrate the complete optional-vector contract offline and make
Milestone 3 implementation completion auditable.

**Implementation**:

- Extend the domain-neutral acceptance fixture with mechanics-only vector and
  Japanese paraphrase cases without treating them as consumer admission data.
- Execute snapshot/chunk loading, lexical index, deterministic embeddings,
  vector sidecar build/open, vector search, entity-level RRF, context/provenance
  serialization, and reproducible evaluation metadata in one offline path.
- Add a subprocess check that package import and lexical CLI behavior work when
  optional embedding modules and model files are unavailable.
- Add failure-path coverage for incompatible fingerprints, sidecar deletion,
  corrupt sidecars, and failed atomic rebuilds.
- Update `docs/architecture.md` or an ADR with any implementation choice that
  resolves an intentionally open decision.

**DoD**:

- [ ] Every Milestone 3 implementation-acceptance bullet maps to at least one
      named executable test.
- [ ] Canonical vector artifacts and hybrid rankings are byte/rank stable across
      two independent builds.
- [ ] Passage, relation, and Claim source roles survive the complete path.
- [ ] Lexical-only import, build, search, and eval remain offline and optional-
      dependency-free.
- [ ] No synthetic result is described as evidence of consumer retrieval value.
- [ ] `PYTHONPATH=src python3 -m pytest` passes.

**Branch**: `test/milestone-3-acceptance`  
**Dependencies**: Issues #1 through #8  
**Labels**: `test`, `priority: high`

## Completion checklist

- [ ] Every issue remains within a one-to-three-day reviewable scope.
- [ ] Every behavior change has executable tests.
- [ ] The lexical baseline works without optional vector dependencies.
- [ ] No LLM client, prompting, HTTP API, authentication, UI, or consumer-domain
      policy enters this package.
- [ ] Every result remains traceable to entity, section, source IDs, and
      applicable Claim status/confidence.
- [ ] Milestone 3 implementation acceptance is complete before any consumer
      admission claim is made.
