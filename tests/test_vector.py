"""Offline unit tests for rag_agent.vector — parsing and validation only.

Anything touching a live Postgres lives in test_vector_integration.py
(gated on a reachable pgvector DB); this suite needs no database and no
network. Connection-less validation paths are exercised with ``conn=None``:
validation raises before any connection access.
"""

import json

import pytest

from rag_agent.vector import (
    EMBEDDING_DIMENSIONS,
    EmbeddingRecord,
    _validate_embedding,
    index_document,
    read_embeddings,
    replace_document,
    similarity_search,
)

BUCKET = "rag-agent-dev-documents-000000000000"


def embeddings_jsonl(*records: dict) -> bytes:
    return "".join(json.dumps(r) + "\n" for r in records).encode("utf-8")


def basis(position: int) -> list[float]:
    """Unit vector with a single 1.0 at ``position`` (within 1024 dims)."""
    assert 0 <= position < EMBEDDING_DIMENSIONS
    return [1.0 if i == position else 0.0 for i in range(EMBEDDING_DIMENSIONS)]


def record(index: int = 0, text: str = "hello", position: int = 0) -> dict:
    return {
        "index": index,
        "text": text,
        "embedding": basis(position),
        "input_text_token_count": 7,
        "start": index * 10,
        "end": index * 10 + 5,
    }


# ---------------------------------------------------------------------------
# _validate_embedding
# ---------------------------------------------------------------------------


def test_validate_embedding_accepts_lists_and_tuples() -> None:
    assert _validate_embedding(basis(3)) == tuple(basis(3))
    assert _validate_embedding(tuple(basis(3))) == tuple(basis(3))


def test_validate_embedding_rejects_wrong_dimensions() -> None:
    with pytest.raises(ValueError, match="expected 1024"):
        _validate_embedding([0.0, 1.0, 2.0])


def test_validate_embedding_rejects_non_numbers() -> None:
    bad = basis(0)
    bad[5] = "x"
    with pytest.raises(ValueError, match="only numbers"):
        _validate_embedding(bad)


# ---------------------------------------------------------------------------
# read_embeddings
# ---------------------------------------------------------------------------


def test_read_embeddings_happy_path() -> None:
    records = read_embeddings(embeddings_jsonl(record(0, "alpha"), record(1, "beta", position=2)))

    assert records == [
        EmbeddingRecord(index=0, text="alpha", embedding=tuple(basis(0)), start=0, end=5),
        EmbeddingRecord(index=1, text="beta", embedding=tuple(basis(2)), start=10, end=15),
    ]


def test_read_embeddings_rejects_empty_file() -> None:
    with pytest.raises(ValueError, match="no records"):
        read_embeddings(b"\n\n")


def test_read_embeddings_names_malformed_line() -> None:
    data = embeddings_jsonl(record(0)) + b"not json\n"
    with pytest.raises(ValueError, match="malformed embedding record on line 2"):
        read_embeddings(data)


def test_read_embeddings_rejects_missing_fields() -> None:
    with pytest.raises(ValueError, match="malformed embedding record on line 1"):
        read_embeddings(embeddings_jsonl({"index": 0}))


def test_read_embeddings_rejects_wrong_dimension_vectors() -> None:
    data = json.dumps({"index": 0, "text": "x", "embedding": [1.0, 2.0, 3.0]}).encode()
    with pytest.raises(ValueError, match="line 1.*expected 1024"):
        read_embeddings(data)


def test_read_embeddings_rejects_non_numeric_vectors() -> None:
    data = json.dumps({"index": 0, "text": "x", "embedding": ["a"] * EMBEDDING_DIMENSIONS}).encode()
    with pytest.raises(ValueError, match="only numbers"):
        read_embeddings(data)


# ---------------------------------------------------------------------------
# Connection-less validation (raises before touching the connection)
# ---------------------------------------------------------------------------


def test_replace_document_validates_before_connecting() -> None:
    with pytest.raises(ValueError, match="document_id"):
        replace_document(None, document_id="", records=[])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="records must not be empty"):
        replace_document(None, document_id="doc", records=[])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="expected 1024"):
        replace_document(
            None,  # type: ignore[arg-type]
            document_id="doc",
            records=[
                EmbeddingRecord(index=0, text="x", embedding=(1.0, 2.0), start=None, end=None)
            ],
        )
    with pytest.raises(ValueError, match="text must be a non-empty string"):
        replace_document(
            None,  # type: ignore[arg-type]
            document_id="doc",
            records=[
                EmbeddingRecord(index=0, text="", embedding=tuple(basis(0)), start=None, end=None)
            ],
        )


def test_similarity_search_validates_before_connecting() -> None:
    with pytest.raises(ValueError, match="expected 1024"):
        similarity_search(None, query_embedding=[1.0, 2.0], top_k=1)  # type: ignore[arg-type]
    for bad in (0, -1, True, 1.5):
        with pytest.raises(ValueError, match="top_k"):
            similarity_search(None, query_embedding=basis(0), top_k=bad)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="document_id"):
        similarity_search(None, query_embedding=basis(0), top_k=1, document_id="")  # type: ignore[arg-type]


class ExplodingS3:
    """Any get_object call is a test failure (must not be reached)."""

    def get_object(self, **kwargs) -> dict:
        raise AssertionError("s3 must not be called for invalid keys")


@pytest.mark.parametrize(
    "bad_key",
    [
        "uploads/doc123/embeddings.jsonl",
        "embedded/doc123",
        "embedded/doc123/chunks.jsonl",
        "embedded/a/b/embeddings.jsonl",
        "doc123/embeddings.jsonl",
    ],
    ids=["wrong-prefix", "no-suffix", "chunks-not-embeddings", "nested-id", "no-prefix"],
)
def test_index_document_rejects_bad_keys_before_io(bad_key: str) -> None:
    with pytest.raises(ValueError, match="embeddings.jsonl|key must be"):
        index_document(BUCKET, bad_key, s3_client=ExplodingS3(), conn=None)  # type: ignore[arg-type]
