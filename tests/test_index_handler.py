"""Tests for the index-document Lambda handler (direct S3 events).

Offline by design: rag_agent.vector.index_document is patched with a fake,
so no boto3 client, psycopg connection, or network is involved. The core is
covered in test_vector.py (offline validation) and test_vector_integration.py
(reach-DB behavior); this suite pins the Chapter 8 handler contract — S3
event in, per-record classification out, DB config resolved lazily.
"""

import index_handler
import pytest

BUCKET = "rag-agent-dev-documents-000000000000"
EMBEDDED_KEY = "embedded/doc123/embeddings.jsonl"


def _record(*, key: str = EMBEDDED_KEY, name: str = "ObjectCreated:Put") -> dict:
    return {
        "eventName": name,
        "s3": {"bucket": {"name": BUCKET}, "object": {"key": key, "size": 1}},
    }


def _event(*records: dict) -> dict:
    return {"Records": list(records)}


@pytest.fixture(autouse=True)
def _stub_clients(monkeypatch) -> None:
    """The real s3/DB builders must never run: _process_record calls them
    before the (patched) core. Stub both; _connect() itself is tested
    directly below."""
    monkeypatch.setattr(index_handler, "_get_s3", lambda: object())
    monkeypatch.setattr(index_handler, "_get_conn", lambda: object())


@pytest.fixture
def fake_index(monkeypatch) -> list[tuple[str, str]]:
    """Replaces index_handler.index_document; records calls, returns a
    canned success summary."""
    calls: list[tuple[str, str]] = []

    def fake(bucket: str, key: str, *, s3_client, conn) -> dict:
        calls.append((bucket, key))
        document_id = key.split("/")[1]
        return {"document_id": document_id, "num_embeddings": 2}

    monkeypatch.setattr(index_handler, "index_document", fake)
    return calls


def test_happy_path_parses_record_and_reports(fake_index) -> None:
    response = index_handler.lambda_handler(_event(_record()), None)

    assert response == {
        "processed": [{"key": EMBEDDED_KEY, "document_id": "doc123", "num_embeddings": 2}],
        "skipped": [],
        "failed": [],
    }
    assert fake_index == [(BUCKET, EMBEDDED_KEY)]


def test_multiple_records_process_independently(fake_index) -> None:
    response = index_handler.lambda_handler(
        _event(
            _record(key="embedded/doc1/embeddings.jsonl"),
            _record(key="embedded/doc2/embeddings.jsonl"),
        ),
        None,
    )

    assert [p["document_id"] for p in response["processed"]] == ["doc1", "doc2"]
    assert len(fake_index) == 2


def test_non_embeddings_key_is_skipped_with_reason(fake_index) -> None:
    response = index_handler.lambda_handler(
        _event(_record(key="embedded/doc123/chunks.jsonl")), None
    )

    assert response["processed"] == []
    assert response["skipped"] == [
        {
            "key": "embedded/doc123/chunks.jsonl",
            "reason": "record key is not an embedded embeddings file",
        }
    ]
    assert fake_index == []


def test_value_error_content_is_skipped_not_retried(monkeypatch) -> None:
    def boom(bucket: str, key: str, *, s3_client, conn) -> dict:
        if "bad" in key:
            raise ValueError("embedding has 3 dimensions; expected 1024")
        return {"document_id": "doc-ok", "num_embeddings": 2}

    monkeypatch.setattr(index_handler, "index_document", boom)

    response = index_handler.lambda_handler(
        _event(
            _record(key="embedded/doc-ok/embeddings.jsonl"),
            _record(key="embedded/doc-bad/embeddings.jsonl"),
        ),
        None,
    )

    assert len(response["processed"]) == 1
    assert response["skipped"] == [
        {
            "key": "embedded/doc-bad/embeddings.jsonl",
            "reason": "embedding has 3 dimensions; expected 1024",
        }
    ]
    assert response["failed"] == []


def test_transient_failure_raises_to_retry_batch(monkeypatch) -> None:
    def boom(bucket: str, key: str, *, s3_client, conn) -> dict:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(index_handler, "index_document", boom)

    with pytest.raises(RuntimeError, match="retrying batch"):
        index_handler.lambda_handler(_event(_record()), None)


def test_non_objectcreated_records_are_ignored(fake_index) -> None:
    response = index_handler.lambda_handler(_event(_record(name="ObjectRemoved:Delete")), None)

    assert response == {"processed": [], "skipped": [], "failed": []}
    assert fake_index == []


def test_record_without_s3_section_is_skipped(fake_index) -> None:
    malformed = {"eventName": "ObjectCreated:Put"}
    response = index_handler.lambda_handler(_event(malformed), None)

    assert response["skipped"][0]["reason"] == "record 's3' section is missing bucket/object"
    assert fake_index == []


def test_empty_event_is_a_noop(fake_index) -> None:
    response = index_handler.lambda_handler({}, None)

    assert response == {"processed": [], "skipped": [], "failed": []}
    assert fake_index == []


def test_missing_db_config_fails_loud(monkeypatch) -> None:
    """No DB_DSN / SECRET_ARN is a deployment error, not a silent skip."""
    monkeypatch.delenv("DB_DSN", raising=False)
    monkeypatch.delenv("SECRET_ARN", raising=False)
    index_handler._conn = None

    with pytest.raises(RuntimeError, match="DB_DSN or SECRET_ARN"):
        index_handler._connect()
