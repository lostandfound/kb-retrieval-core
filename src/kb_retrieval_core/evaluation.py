"""Deterministic retrieval evaluation without an LLM."""

from __future__ import annotations

import inspect
import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping, Protocol

from .models import SearchHit
from ._normalization import normalize_json

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    yaml = None  # type: ignore[assignment]
    _YAML_IMPORT_ERROR = exc
else:
    _YAML_IMPORT_ERROR = None


class EvaluationLoadError(ValueError):
    """Raised when an evaluation file violates its schema."""


class EvaluationError(ValueError):
    """Raised when retriever output cannot be evaluated safely."""


class Retriever(Protocol):
    def __call__(self, query: str, **kwargs: object) -> Iterable[SearchHit]: ...


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    query: str
    expected_paths: tuple[str, ...]
    case_id: str | None = None
    kind: str | None = None
    expected: object | None = None
    history: tuple[Mapping[str, object], ...] = ()
    gap: object | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.query, str) or not self.query.strip():
            raise ValueError("query must be a non-empty string")
        if self.expected is not None and (not isinstance(self.expected, str) or not self.expected.strip()):
            raise ValueError("expected must be a non-empty string when provided")
        # Scoring uses unique expected paths while retaining first-seen order.
        paths = tuple(dict.fromkeys(self.expected_paths))
        if not paths or any(not isinstance(path, str) or not path.startswith("/") or not path.strip() for path in paths):
            raise ValueError("expected_paths must contain bundle-root-relative paths")
        if self.case_id is not None and (not isinstance(self.case_id, str) or not self.case_id.strip()):
            raise ValueError("case_id must be a non-empty string or None")
        if self.kind is not None and (not isinstance(self.kind, str) or not self.kind.strip()):
            raise ValueError("kind must be a non-empty string or None")
        if any(not isinstance(item, Mapping) for item in self.history):
            raise TypeError("history must contain mappings")
        object.__setattr__(self, "expected_paths", paths)
        object.__setattr__(self, "history", tuple(self.history))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @property
    def expected_evidence_paths(self) -> tuple[str, ...]:
        return self.expected_paths

    @property
    def query_kind(self) -> str | None:
        return self.kind

    @property
    def id(self) -> str | None:
        return self.case_id


@dataclass(frozen=True, slots=True)
class RetrievedResult:
    entity_path: str
    score: float
    rank: int
    retriever: str
    evidence_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.entity_path, str) or not self.entity_path.startswith("/"):
            raise ValueError("entity_path must be bundle-root-relative")
        if isinstance(self.rank, bool) or not isinstance(self.rank, int) or self.rank < 1:
            raise ValueError("rank must be at least 1")
        if isinstance(self.score, bool) or not isinstance(self.score, (int, float)) or not math.isfinite(float(self.score)):
            raise ValueError("score must be finite")
        if not isinstance(self.retriever, str) or not self.retriever.strip():
            raise ValueError("retriever must not be empty")
        paths = tuple(dict.fromkeys((self.entity_path, *self.evidence_paths)))
        if any(not isinstance(path, str) or not path.startswith("/") for path in paths):
            raise ValueError("evidence_paths must be bundle-root-relative")
        object.__setattr__(self, "score", float(self.score))
        object.__setattr__(self, "evidence_paths", paths)

    @property
    def path(self) -> str:
        return self.entity_path


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    case: EvaluationCase
    retrieved: tuple[RetrievedResult, ...]
    recall_at_k: float
    reciprocal_rank: float
    missing_paths: tuple[str, ...]
    success: bool

    def __post_init__(self) -> None:
        if not 0 <= self.recall_at_k <= 1 or not math.isfinite(self.recall_at_k):
            raise ValueError("recall_at_k must be between 0 and 1")
        if not 0 <= self.reciprocal_rank <= 1 or not math.isfinite(self.reciprocal_rank):
            raise ValueError("reciprocal_rank must be between 0 and 1")
        object.__setattr__(self, "retrieved", tuple(self.retrieved))
        object.__setattr__(self, "missing_paths", tuple(self.missing_paths))

    @property
    def query(self) -> str:
        return self.case.query

    @property
    def mrr(self) -> float:
        return self.reciprocal_rank

    @property
    def retrieved_paths(self) -> tuple[str, ...]:
        return tuple(result.entity_path for result in self.retrieved)

    @property
    def expected_paths(self) -> tuple[str, ...]:
        return self.case.expected_paths

    @property
    def missing_expected_paths(self) -> tuple[str, ...]:
        return self.missing_paths


@dataclass(frozen=True, slots=True)
class EvaluationAggregate:
    count: int
    recall_at_k: float
    reciprocal_rank: float
    successes: int

    def __post_init__(self) -> None:
        if not isinstance(self.count, int) or self.count < 1:
            raise ValueError("count must be positive")
        if not 0 <= self.recall_at_k <= 1 or not math.isfinite(self.recall_at_k):
            raise ValueError("recall_at_k must be between 0 and 1")
        if not 0 <= self.reciprocal_rank <= 1 or not math.isfinite(self.reciprocal_rank):
            raise ValueError("reciprocal_rank must be between 0 and 1")
        if not isinstance(self.successes, int) or not 0 <= self.successes <= self.count:
            raise ValueError("successes must be between zero and count")

    @property
    def mrr(self) -> float:
        return self.reciprocal_rank


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    k: int
    results: tuple[EvaluationResult, ...]
    recall_at_k: float
    reciprocal_rank: float
    by_kind: Mapping[str, EvaluationAggregate] = field(default_factory=dict)
    retrieval_mode: str | None = None
    snapshot_hash: str | None = None
    evaluation_case_hash: str | None = None
    package_identity: str | None = None
    embedding_fingerprint: str | None = None
    vector_index_fingerprint: str | None = None
    fusion_config: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.k, bool) or not isinstance(self.k, int) or self.k < 1:
            raise ValueError("k must be a positive integer")
        results = tuple(self.results)
        if not results:
            raise ValueError("results must not be empty")
        if not 0 <= self.recall_at_k <= 1 or not 0 <= self.reciprocal_rank <= 1:
            raise ValueError("aggregate metrics must be between 0 and 1")
        object.__setattr__(self, "results", results)
        object.__setattr__(self, "by_kind", MappingProxyType(dict(sorted(self.by_kind.items()))))
        if self.retrieval_mode is not None and (not isinstance(self.retrieval_mode, str) or not self.retrieval_mode.strip()):
            raise ValueError("retrieval_mode must be a non-empty string or None")
        for name in ("snapshot_hash", "evaluation_case_hash", "package_identity", "embedding_fingerprint", "vector_index_fingerprint"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be a non-empty string or None")
        if not isinstance(self.fusion_config, Mapping):
            raise TypeError("fusion_config must be a mapping")
        object.__setattr__(self, "fusion_config", MappingProxyType(dict(self.fusion_config)))

    @property
    def mrr(self) -> float:
        return self.reciprocal_rank

    @property
    def cases(self) -> tuple[EvaluationCase, ...]:
        return tuple(result.case for result in self.results)

    @property
    def kind_metrics(self) -> Mapping[str, EvaluationAggregate]:
        return self.by_kind


@dataclass(frozen=True, slots=True)
class EvaluationProfile:
    """Consumer-owned admission thresholds for comparing retrieval reports."""

    name: str
    primary_metric: str = "recall_at_k"
    minimum_improvement: float = 0.0
    maximum_mrr_regression: float = 0.0
    maximum_per_query_regressions: int = 0
    require_reproducible_metadata: bool = True
    require_lexical_gap_success: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("profile name must be non-empty")
        if self.primary_metric not in {"recall_at_k", "reciprocal_rank"}:
            raise ValueError("primary_metric must be recall_at_k or reciprocal_rank")
        for field_name in ("minimum_improvement", "maximum_mrr_regression"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0:
                raise ValueError(f"{field_name} must be a finite non-negative number")
        if isinstance(self.maximum_per_query_regressions, bool) or not isinstance(self.maximum_per_query_regressions, int) or self.maximum_per_query_regressions < 0:
            raise ValueError("maximum_per_query_regressions must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class EvaluationComparison:
    baseline: EvaluationReport
    candidate: EvaluationReport
    profile: EvaluationProfile
    admitted: bool
    diagnostics: tuple[str, ...]
    primary_improvement: float
    mrr_delta: float
    per_query_regressions: int


def compare_evaluations(baseline: EvaluationReport, candidate: EvaluationReport, profile: EvaluationProfile) -> EvaluationComparison:
    if not isinstance(baseline, EvaluationReport) or not isinstance(candidate, EvaluationReport):
        raise TypeError("baseline and candidate must be EvaluationReport objects")
    if not isinstance(profile, EvaluationProfile):
        raise TypeError("profile must be an EvaluationProfile")
    diagnostics: list[str] = []
    if baseline.k != candidate.k:
        diagnostics.append("cutoff k differs")
    if baseline.evaluation_case_hash != candidate.evaluation_case_hash:
        diagnostics.append("evaluation case hash differs")
    if baseline.snapshot_hash != candidate.snapshot_hash:
        diagnostics.append("snapshot hash differs")
    if profile.require_reproducible_metadata:
        for label, report in (("baseline", baseline), ("candidate", candidate)):
            if not report.evaluation_case_hash or not report.snapshot_hash or not report.retrieval_mode or not report.package_identity:
                diagnostics.append(f"{label} report lacks reproducibility metadata")
        if candidate.retrieval_mode in {"vector", "hybrid"}:
            if not candidate.embedding_fingerprint or not candidate.vector_index_fingerprint:
                diagnostics.append("candidate report lacks embedding/vector fingerprints")
            if not candidate.fusion_config:
                diagnostics.append("candidate report lacks fusion configuration")
    baseline_primary = getattr(baseline, profile.primary_metric)
    candidate_primary = getattr(candidate, profile.primary_metric)
    improvement = float(candidate_primary - baseline_primary)
    mrr_delta = float(candidate.mrr - baseline.mrr)
    regressions = sum(
        candidate_result.recall_at_k < baseline_result.recall_at_k
        for baseline_result, candidate_result in zip(baseline.results, candidate.results)
    )
    if improvement < profile.minimum_improvement:
        diagnostics.append(f"primary improvement {improvement:.6g} below minimum {profile.minimum_improvement:.6g}")
    if mrr_delta < -profile.maximum_mrr_regression:
        diagnostics.append("MRR regression exceeds tolerance")
    if regressions > profile.maximum_per_query_regressions:
        diagnostics.append("per-query regressions exceed tolerance")
    if profile.require_lexical_gap_success:
        gap_improvements = any(
            baseline_result.case.gap is not None
            and not baseline_result.success
            and candidate_result.success
            for baseline_result, candidate_result in zip(baseline.results, candidate.results)
        )
        if not gap_improvements:
            diagnostics.append("candidate does not improve any lexical-gap case")
    return EvaluationComparison(baseline, candidate, profile, not diagnostics, tuple(diagnostics), improvement, mrr_delta, regressions)


def load_evaluation_cases(path: str | Path) -> tuple[EvaluationCase, ...]:
    path = Path(path)
    if not path.exists():
        raise EvaluationLoadError(f"{path}: file does not exist")
    if yaml is None:
        raise EvaluationLoadError(f"{path}: YAML support requires PyYAML: {_YAML_IMPORT_ERROR}")
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except (OSError, yaml.YAMLError) as exc:
        raise EvaluationLoadError(f"{path}: malformed YAML: {exc}") from exc
    if isinstance(value, Mapping):
        wrappers = [key for key in ("cases", "evals", "queries") if key in value]
        if len(wrappers) != 1:
            raise EvaluationLoadError(f"{path}: top-level YAML must be a list")
        value = value[wrappers[0]]
    if not isinstance(value, list) or not value:
        raise EvaluationLoadError(f"{path}: evaluation data must be a non-empty list")
    cases: list[EvaluationCase] = []
    seen: set[str] = set()
    for index, record in enumerate(value):
        if not isinstance(record, Mapping):
            raise EvaluationLoadError(f"{path}: case {index} must be a mapping")
        case_id = _alias(record, ("id", "case_id", "name"), path, index, required=True)
        if not isinstance(case_id, str) or not case_id.strip():
            raise EvaluationLoadError(f"{path}: case {index} field 'id' must be a non-empty string")
        query = _alias(record, ("query", "question"), path, index, required=True)
        if not isinstance(query, str) or not query.strip():
            raise EvaluationLoadError(f"{path}: case {index} field 'query' must be a non-empty string")
        if "expected" not in record:
            raise EvaluationLoadError(f"{path}: case {index} missing required field 'expected'")
        expected = record["expected"]
        if not isinstance(expected, str) or not expected.strip():
            raise EvaluationLoadError(f"{path}: case {index} field 'expected' must be a non-empty string")
        evidence = _alias(record, ("evidence", "expected_paths", "expected_paths", "expected_evidence_paths", "expected_entities", "evidence_paths"), path, index, required=True)
        if not isinstance(evidence, (list, tuple)):
            raise EvaluationLoadError(f"{path}: case {index} evidence must be a list")
        kind = _alias(record, ("kind", "query_kind"), path, index, required=False)
        if case_id in seen:
            raise EvaluationLoadError(f"{path}: duplicate case ID {case_id!r}")
        seen.add(case_id)
        history = record.get("history", ())
        if history is None:
            history = ()
        if not isinstance(history, (list, tuple)):
            raise EvaluationLoadError(f"{path}: case {index} history must be a list")
        known = {"id", "case_id", "name", "query", "question", "expected", "evidence", "expected_paths", "expected_evidence_paths", "expected_entities", "evidence_paths", "history", "kind", "query_kind", "gap"}
        metadata = {key: value for key, value in record.items() if key not in known}
        try:
            cases.append(EvaluationCase(str(query), tuple(evidence), str(case_id), None if kind is None else str(kind), expected, tuple(history), record.get("gap"), metadata))
        except (TypeError, ValueError) as exc:
            raise EvaluationLoadError(f"{path}: invalid case {index}: {exc}") from exc
    return tuple(cases)


def _alias(record: Mapping[str, object], names: tuple[str, ...], path: Path, index: int, *, required: bool) -> object:
    present = [name for name in names if name in record]
    if len(present) > 1 and any(record[name] != record[present[0]] for name in present[1:]):
        raise EvaluationLoadError(f"{path}: case {index} conflicting aliases {present}")
    if not present:
        if required:
            raise EvaluationLoadError(f"{path}: case {index} missing required field {names[0]!r}")
        return None
    return record[present[0]]


def evaluate(
    cases: Iterable[EvaluationCase],
    retriever: Callable[..., Iterable[SearchHit]],
    *,
    k: int = 5,
    retrieval_mode: str | None = None,
    snapshot_hash: str | None = None,
    package_identity: str | None = None,
    embedding_fingerprint: str | None = None,
    vector_index_fingerprint: str | None = None,
    fusion_config: Mapping[str, object] | None = None,
) -> EvaluationReport:
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ValueError("k must be a positive integer")
    cases = tuple(cases)
    if not cases:
        raise ValueError("cases must not be empty")
    results: list[EvaluationResult] = []
    for case in cases:
        try:
            hits = _call_retriever(retriever, case.query, k)
        except Exception as exc:
            raise EvaluationError(f"retriever failed for {case.case_id or case.query!r}: {exc}") from exc
        hits = tuple(hits)
        if any(not isinstance(hit, SearchHit) for hit in hits):
            raise EvaluationError(f"retriever returned a non-SearchHit for {case.case_id or case.query!r}")
        hits = tuple(hit for hit in hits if hit.rank <= k)[:k]
        retrieved = tuple(
            RetrievedResult(
                hit.evidence.entity_path,
                hit.score,
                rank,
                hit.retriever,
                _hit_evidence_paths(hit),
            )
            for rank, hit in enumerate(hits, 1)
        )
        expected = set(case.expected_paths)
        found = {path for item in retrieved for path in item.evidence_paths} & expected
        missing = tuple(path for path in case.expected_paths if path not in found)
        recall = len(found) / len(expected)
        first = next((item.rank for item in retrieved if expected.intersection(item.evidence_paths)), None)
        mrr = 0.0 if first is None else 1.0 / first
        results.append(EvaluationResult(case, retrieved, recall, mrr, missing, not missing))
    recall = sum(result.recall_at_k for result in results) / len(results)
    mrr = sum(result.reciprocal_rank for result in results) / len(results)
    by_kind: dict[str, EvaluationAggregate] = {}
    groups: dict[str, list[EvaluationResult]] = {}
    for result in results:
        if result.case.kind is not None:
            groups.setdefault(result.case.kind, []).append(result)
    for kind, grouped in groups.items():
        by_kind[kind] = EvaluationAggregate(len(grouped), sum(item.recall_at_k for item in grouped) / len(grouped), sum(item.reciprocal_rank for item in grouped) / len(grouped), sum(item.success for item in grouped))
    case_hash = _evaluation_case_hash(cases)
    return EvaluationReport(
        k, tuple(results), recall, mrr, by_kind,
        retrieval_mode, snapshot_hash, case_hash, package_identity,
        embedding_fingerprint, vector_index_fingerprint, fusion_config or {},
    )


def _hit_evidence_paths(hit: SearchHit) -> tuple[str, ...]:
    paths = [hit.evidence.entity_path]
    claim_path = hit.evidence.metadata.get("claim_path")
    if isinstance(claim_path, str) and claim_path.startswith("/"):
        paths.append(claim_path)
    return tuple(paths)


def _evaluation_case_hash(cases: Iterable[EvaluationCase]) -> str:
    payload = []
    for case in cases:
        payload.append({
            "id": case.case_id,
            "query": case.query,
            "expected_paths": list(case.expected_paths),
            "kind": case.kind,
            "expected": case.expected,
            "history": list(case.history),
            "gap": case.gap,
            "metadata": dict(case.metadata),
        })
    canonical = json.dumps(normalize_json(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _call_retriever(retriever: Callable[..., Iterable[SearchHit]], query: str, k: int) -> Iterable[SearchHit]:
    supports_top_k = False
    try:
        signature = inspect.signature(retriever)
        supports_top_k = "top_k" in signature.parameters
    except (TypeError, ValueError):
        supports_top_k = False
    if supports_top_k:
        return retriever(query, top_k=k)
    return retriever(query)


__all__ = ["EvaluationAggregate", "EvaluationCase", "EvaluationComparison", "EvaluationError", "EvaluationLoadError", "EvaluationProfile", "EvaluationReport", "EvaluationResult", "RetrievedResult", "compare_evaluations", "evaluate", "load_evaluation_cases"]
