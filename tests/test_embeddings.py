from __future__ import annotations

from dataclasses import replace
from math import inf, nan
from struct import pack

import pytest

from kb_retrieval_core import (
    DeterministicTestEmbedder,
    Embedder,
    EmbeddedDocument,
    EmbeddingConfig,
    EmbeddingDocument,
    EmbeddingProviderError,
    InjectedEmbedder,
    VectorIndexConfig,
    embedding_config_to_dict,
    embedding_fingerprint,
    validate_document_embeddings,
    validate_query_embedding,
    vector_index_fingerprint,
)


def _config(**changes: object) -> EmbeddingConfig:
    values: dict[str, object] = {
        "implementation": "local-test",
        "model": "fixture-embedder",
        "dimension": 3,
        "revision": "v1",
        "document_task": "document",
        "query_task": "query",
        "tokenizer": "unicode-v1",
        "preprocessing": {"nfkc": True, "prefixes": ["passage", "query"]},
        "pooling": "mean",
        "normalization": "l2",
    }
    values.update(changes)
    return EmbeddingConfig(**values)  # type: ignore[arg-type]


def test_embedding_fingerprint_is_canonical_and_configuration_is_immutable() -> None:
    first = _config(preprocessing={"nfkc": True, "prefixes": ["passage", "query"]})
    second = _config(preprocessing={"prefixes": ("passage", "query"), "nfkc": True})
    assert first.fingerprint == second.fingerprint
    assert first.fingerprint == embedding_fingerprint(first)
    assert len(first.fingerprint) == 64
    assert embedding_config_to_dict(first)["preprocessing"] == {
        "nfkc": True,
        "prefixes": ["passage", "query"],
    }
    with pytest.raises(TypeError):
        first.preprocessing["new"] = True  # type: ignore[index]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("implementation", "other"),
        ("model", "other"),
        ("dimension", 4),
        ("revision", "v2"),
        ("document_task", "passage"),
        ("query_task", "search"),
        ("tokenizer", "unicode-v2"),
        ("preprocessing", {"nfkc": False}),
        ("pooling", "cls"),
        ("normalization", "none"),
    ],
)
def test_every_compatibility_field_changes_embedding_fingerprint(field: str, value: object) -> None:
    original = _config()
    changed = replace(original, **{field: value})
    assert changed.fingerprint != original.fingerprint


def test_vector_index_fingerprint_includes_metric_and_format() -> None:
    embedding = _config()
    first = VectorIndexConfig(embedding, "COSINE", 1)
    equivalent = VectorIndexConfig(embedding, "cosine", 1)
    other_metric = VectorIndexConfig(embedding, "dot", 1)
    other_format = VectorIndexConfig(embedding, "cosine", 2)
    assert first.similarity_metric == "cosine"
    assert first.fingerprint == equivalent.fingerprint
    assert first.fingerprint == vector_index_fingerprint(first)
    assert first.fingerprint != other_metric.fingerprint
    assert first.fingerprint != other_format.fingerprint


def test_embedding_config_rejects_invalid_identity_and_json_configuration() -> None:
    with pytest.raises(ValueError, match="implementation"):
        _config(implementation="  ")
    with pytest.raises(TypeError, match="dimension"):
        _config(dimension=True)
    with pytest.raises(ValueError, match="dimension"):
        _config(dimension=0)
    with pytest.raises(TypeError, match="preprocessing"):
        _config(preprocessing=[])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="non-finite"):
        _config(preprocessing={"threshold": nan})
    with pytest.raises(TypeError, match="keys"):
        _config(preprocessing={1: "invalid"})  # type: ignore[dict-item]


def test_validate_document_embeddings_preserves_exact_identity_order_and_dimension() -> None:
    documents = (
        EmbeddingDocument("chunk-1", "first"),
        EmbeddingDocument("chunk-2", "second"),
    )
    results = (
        EmbeddedDocument("chunk-1", (1, 2, 3)),
        EmbeddedDocument("chunk-2", (4.0, 5.0, 6.0)),
    )
    validated = validate_document_embeddings(documents, results, dimension=3)
    assert validated == results
    assert validated[0].vector == (1.0, 2.0, 3.0)

    with pytest.raises(ValueError, match="count"):
        validate_document_embeddings(documents, results[:1], dimension=3)
    with pytest.raises(ValueError, match="order"):
        validate_document_embeddings(documents, tuple(reversed(results)), dimension=3)
    with pytest.raises(ValueError, match="dimension"):
        validate_document_embeddings(
            documents,
            (results[0], EmbeddedDocument("chunk-2", (1.0, 2.0))),
            dimension=3,
        )


def test_validate_document_embeddings_rejects_duplicate_ids_and_wrong_types() -> None:
    duplicate_documents = (
        EmbeddingDocument("same", "first"),
        EmbeddingDocument("same", "second"),
    )
    duplicate_results = (
        EmbeddedDocument("same", (1.0,)),
        EmbeddedDocument("same", (2.0,)),
    )
    with pytest.raises(ValueError, match="document IDs"):
        validate_document_embeddings(duplicate_documents, duplicate_results, dimension=1)

    documents = (EmbeddingDocument("one", "first"), EmbeddingDocument("two", "second"))
    with pytest.raises(ValueError, match="result document IDs"):
        validate_document_embeddings(documents, duplicate_results, dimension=1)
    with pytest.raises(TypeError, match="EmbeddingDocument"):
        validate_document_embeddings(("not-a-document",), (), dimension=1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="EmbeddedDocument"):
        validate_document_embeddings(documents, ("not-a-result",), dimension=1)  # type: ignore[arg-type]


@pytest.mark.parametrize("vector", [(1.0, nan), (1.0, inf), (1.0, -inf)])
def test_vectors_reject_non_finite_values(vector: tuple[float, ...]) -> None:
    with pytest.raises(ValueError, match="finite"):
        EmbeddedDocument("chunk", vector)
    with pytest.raises(ValueError, match="finite"):
        validate_query_embedding(vector, dimension=2)


@pytest.mark.parametrize("vector", [(1.0, "two"), (1.0, True), "text"])
def test_vectors_reject_non_numeric_values(vector: object) -> None:
    with pytest.raises(TypeError, match="numbers"):
        EmbeddedDocument("chunk", vector)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="numbers"):
        validate_query_embedding(vector, dimension=2)  # type: ignore[arg-type]


def test_query_validation_is_independent_from_document_validation() -> None:
    assert validate_query_embedding([1, 2, 3], dimension=3) == (1.0, 2.0, 3.0)
    with pytest.raises(ValueError, match="dimension"):
        validate_query_embedding([1, 2], dimension=3)


def test_embedder_protocol_exposes_distinct_document_and_query_methods() -> None:
    class ExampleEmbedder:
        config = _config()

        def embed_documents(self, documents):
            return tuple(EmbeddedDocument(item.document_id, (1.0, 0.0, 0.0)) for item in documents)

        def embed_query(self, text):
            return (0.0, 1.0, 0.0)

    embedder = ExampleEmbedder()
    assert isinstance(embedder, Embedder)
    documents = (EmbeddingDocument("one", "document"),)
    assert embedder.embed_documents(documents)[0].document_id == "one"
    assert embedder.embed_query("query") != embedder.embed_documents(documents)[0].vector


def test_injected_embedder_is_side_effect_free_and_validates_both_paths() -> None:
    calls: list[tuple[str, object]] = []

    def documents(values):
        calls.append(("documents", values))
        return tuple(
            EmbeddedDocument(item.document_id, (1.0, 2.0, 3.0)) for item in values
        )

    def query(text):
        calls.append(("query", text))
        return (3.0, 2.0, 1.0)

    embedder = InjectedEmbedder(
        _config(), document_embedder=documents, query_embedder=query
    )
    assert calls == []
    requested = (EmbeddingDocument("one", "first"),)
    assert embedder.embed_documents(requested) == (
        EmbeddedDocument("one", (1.0, 2.0, 3.0)),
    )
    assert embedder.embed_query("question") == (3.0, 2.0, 1.0)
    assert calls == [("documents", requested), ("query", "question")]
    assert isinstance(embedder, Embedder)


def test_injected_embedder_rejects_invalid_provider_outputs() -> None:
    config = _config()
    requested = (
        EmbeddingDocument("one", "first"),
        EmbeddingDocument("two", "second"),
    )
    reordered = InjectedEmbedder(
        config,
        document_embedder=lambda _values: (
            EmbeddedDocument("two", (1.0, 2.0, 3.0)),
            EmbeddedDocument("one", (1.0, 2.0, 3.0)),
        ),
        query_embedder=lambda _text: (1.0, 2.0, 3.0),
    )
    with pytest.raises(ValueError, match="order"):
        reordered.embed_documents(requested)

    wrong_dimension = InjectedEmbedder(
        config,
        document_embedder=lambda values: tuple(
            EmbeddedDocument(item.document_id, (1.0, 2.0)) for item in values
        ),
        query_embedder=lambda _text: (1.0, 2.0),
    )
    with pytest.raises(ValueError, match="dimension"):
        wrong_dimension.embed_documents(requested)
    with pytest.raises(ValueError, match="dimension"):
        wrong_dimension.embed_query("question")


def test_injected_embedder_preserves_provider_exception_cause() -> None:
    document_failure = LookupError("document model unavailable")
    query_failure = ConnectionError("query model unavailable")

    def fail_documents(_values):
        raise document_failure

    def fail_query(_text):
        raise query_failure

    embedder = InjectedEmbedder(
        _config(), document_embedder=fail_documents, query_embedder=fail_query
    )
    with pytest.raises(EmbeddingProviderError, match="document model unavailable") as document_error:
        embedder.embed_documents((EmbeddingDocument("one", "text"),))
    assert document_error.value.__cause__ is document_failure
    with pytest.raises(EmbeddingProviderError, match="query model unavailable") as query_error:
        embedder.embed_query("question")
    assert query_error.value.__cause__ is query_failure


def test_injected_embedder_validates_construction_and_input_before_calling_provider() -> None:
    with pytest.raises(TypeError, match="document_embedder"):
        InjectedEmbedder(_config(), document_embedder=None, query_embedder=lambda _text: ())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="query_embedder"):
        InjectedEmbedder(_config(), document_embedder=lambda _values: (), query_embedder=None)  # type: ignore[arg-type]

    called = False

    def query(_text):
        nonlocal called
        called = True
        return (1.0, 2.0, 3.0)

    embedder = InjectedEmbedder(
        _config(), document_embedder=lambda _values: (), query_embedder=query
    )
    with pytest.raises(ValueError, match="must not be empty"):
        embedder.embed_query("  ")
    assert not called


def test_deterministic_test_embedder_is_stable_ordered_and_role_specific() -> None:
    config = _config(dimension=19)
    first = DeterministicTestEmbedder(config)
    second = DeterministicTestEmbedder(config)
    documents = (
        EmbeddingDocument("one", "same text"),
        EmbeddingDocument("two", "different text"),
    )
    first_results = first.embed_documents(documents)
    second_results = second.embed_documents(documents)
    assert first_results == second_results
    assert pack("!19d", *first_results[0].vector) == pack(
        "!19d", *second_results[0].vector
    )
    assert tuple(item.document_id for item in first_results) == ("one", "two")
    assert all(len(item.vector) == 19 for item in first_results)
    assert first.embed_query("same text") != first_results[0].vector
    assert first.embed_query("same text") == second.embed_query("same text")
    assert first_results != DeterministicTestEmbedder(
        replace(config, revision="v2")
    ).embed_documents(documents)


def test_deterministic_test_embedder_handles_empty_batch_and_duplicate_ids() -> None:
    embedder = DeterministicTestEmbedder(_config())
    assert embedder.embed_documents(()) == ()
    with pytest.raises(ValueError, match="unique"):
        embedder.embed_documents(
            (
                EmbeddingDocument("same", "first"),
                EmbeddingDocument("same", "second"),
            )
        )
