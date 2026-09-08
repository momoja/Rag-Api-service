"""Tests for the embed-document Lambda handler (direct S3 events).

Offline by design: rag_agent.embed.embed_document is patched with a fake,
so no boto3 client, credentials, or network is involved. The embedding core
is covered end-to-end in test_embed.py with fake Bedrock/S3 clients; this
suite pins the Chapter 7 handler contract — S3 event in, per-record
classification (processed / skipped / retried) out.
"""

import embed_handler
import pytest

BUCKET = "rag-agent-dev-documents-000000000000"
CHUNKS_KEY = "processed/doc123/chunks.jsonl"


def _record(*, key: str = CHUNKS_KEY, name: str = "ObjectCreated:Put") -> dict:
    return {
        "eventName": name,
        "s3": {"bucket": {"name": BUCKET}, "object": {"key": key, "size": 1}},
    }


def _event(*records: dict) -> dict:
    return {"Records": list(records)}


@pytest.fixture
def fake_embed(monkeypatch) -> list[tuple[str, str]]:
    """Replaces embed_handler.embed_document; records calls, returns a
    canned success summary."""
    calls: list[tuple[str, str]] = []

    def fake(bucket: str, chunks_key: str) -> dict:
        calls.append((bucket, chunks_key))
        document_id = chunks_key.split("/")[1]
        return {"document_id": document_id, "num_embeddings": 4}

    monkeypatch.setattr(embed_handler, "embed_document", fake)
    return calls


def test_happy_path_parses_record_and_reports(fake_embed) -> None:
    response = embed_handler.lambda_handler(_event(_record()), None)

    assert response == {
        "processed": [{"key": CHUNKS_KEY, "document_id": "doc123", "num_embeddings": 4}],
        "skipped": [],
        "failed": [],
    }
    assert fake_embed == [(BUCKET, CHUNKS_KEY)]


def test_multiple_records_process_independently(fake_embed) -> None:
    response = embed_handler.lambda_handler(
        _event(
            _record(key="processed/doc1/chunks.jsonl"), _record(key="processed/doc2/chunks.jsonl")
        ),
        None,
    )

    assert len(response["processed"]) == 2
    assert [p["document_id"] for p in response["processed"]] == ["doc1", "doc2"]
    assert len(fake_embed) == 2


def test_non_chunks_key_is_skipped_with_reason(fake_embed) -> None:
    response = embed_handler.lambda_handler(
        _event(_record(key="processed/doc123/metadata.json")), None
    )

    assert response["processed"] == []
    assert response["skipped"] == [
        {
            "key": "processed/doc123/metadata.json",
            "reason": "record key is not a processed chunks file",
        }
    ]
    assert fake_embed == []


def test_value_error_content_is_skipped_not_retried(monkeypatch) -> None:
    def boom(bucket: str, chunks_key: str) -> dict:
        if "bad" in chunks_key:
            raise ValueError("chunks file contains no chunk records")
        return {"document_id": "doc-ok", "num_embeddings": 2}

    monkeypatch.setattr(embed_handler, "embed_document", boom)

    response = embed_handler.lambda_handler(
        _event(
            _record(key="processed/doc-ok/chunks.jsonl"),
            _record(key="processed/doc-bad/chunks.jsonl"),
        ),
        None,
    )

    assert len(response["processed"]) == 1
    assert response["skipped"] == [
        {"key": "processed/doc-bad/chunks.jsonl", "reason": "chunks file contains no chunk records"}
    ]
    assert response["failed"] == []


def test_transient_failure_raises_to_retry_batch(monkeypatch) -> None:
    def boom(bucket: str, chunks_key: str) -> dict:
        raise RuntimeError("bedrock throttled past retries")

    monkeypatch.setattr(embed_handler, "embed_document", boom)

    with pytest.raises(RuntimeError, match="retrying batch"):
        embed_handler.lambda_handler(_event(_record()), None)


def test_non_objectcreated_records_are_ignored(fake_embed) -> None:
    response = embed_handler.lambda_handler(_event(_record(name="ObjectRemoved:Delete")), None)

    assert response == {"processed": [], "skipped": [], "failed": []}
    assert fake_embed == []


def test_record_without_s3_section_is_skipped(fake_embed) -> None:
    malformed = {"eventName": "ObjectCreated:Put"}
    response = embed_handler.lambda_handler(_event(malformed), None)

    assert response["skipped"][0]["reason"] == "record 's3' section is missing bucket/object"
    assert fake_embed == []


def test_empty_event_is_a_noop(fake_embed) -> None:
    response = embed_handler.lambda_handler({}, None)

    assert response == {"processed": [], "skipped": [], "failed": []}
    assert fake_embed == []
