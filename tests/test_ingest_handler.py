"""Tests for the ingest-document Lambda handler (direct S3 events).

Offline by design: rag_agent.ingest.process_document is patched with a fake,
so no boto3 client, credentials, or network is involved. The core pipeline
itself is covered end-to-end in test_ingest.py with a fake S3 client; this
suite pins the Chapter 6 handler contract — S3 event in, per-record
classification (processed / skipped / retried) out.
"""

import ingest_handler
import pytest

BUCKET = "rag-agent-dev-documents-000000000000"


def _record(
    *,
    key: str = "uploads/report.pdf",
    version: str | None = "v1",
    name: str = "ObjectCreated:Put",
    bucket: str = BUCKET,
) -> dict:
    record: dict = {"eventName": name, "s3": {"bucket": {"name": bucket}, "object": {"key": key}}}
    if version is not None:
        record["s3"]["object"]["versionId"] = version
    return record


def _event(*records: dict) -> dict:
    return {"Records": list(records)}


@pytest.fixture
def fake_process(monkeypatch) -> list[dict]:
    """Replaces ingest_handler.process_document; records calls, returns a
    canned success summary."""
    calls: list[dict] = []

    def fake(
        bucket: str, key: str, version_id: str | None, *, chunk_size: int, overlap: int
    ) -> dict:
        calls.append(
            {
                "bucket": bucket,
                "key": key,
                "version_id": version_id,
                "chunk_size": chunk_size,
                "overlap": overlap,
            }
        )
        return {"document_id": "doc-abc", "num_chunks": 3}

    monkeypatch.setattr(ingest_handler, "process_document", fake)
    return calls


def test_happy_path_parses_record_and_reports(fake_process) -> None:
    response = ingest_handler.lambda_handler(_event(_record()), None)

    assert response == {
        "processed": [
            {
                "key": "uploads/report.pdf",
                "version_id": "v1",
                "document_id": "doc-abc",
                "num_chunks": 3,
            }
        ],
        "skipped": [],
        "failed": [],
    }
    assert fake_process == [
        {
            "bucket": BUCKET,
            "key": "uploads/report.pdf",
            "version_id": "v1",
            "chunk_size": 1200,
            "overlap": 200,
        }
    ]


def test_url_encoded_key_is_decoded(fake_process) -> None:
    ingest_handler.lambda_handler(_event(_record(key="uploads/my%20report.pdf")), None)

    assert fake_process[0]["key"] == "uploads/my report.pdf"
    assert fake_process[0]["version_id"] == "v1"


def test_missing_version_id_passes_none(fake_process) -> None:
    ingest_handler.lambda_handler(_event(_record(version=None)), None)

    assert fake_process[0]["version_id"] is None


def test_multiple_records_process_independently(fake_process) -> None:
    response = ingest_handler.lambda_handler(
        _event(
            _record(key="uploads/a.pdf", version="v1"), _record(key="uploads/b.md", version="v2")
        ),
        None,
    )

    assert len(response["processed"]) == 2
    assert [p["key"] for p in response["processed"]] == ["uploads/a.pdf", "uploads/b.md"]
    assert response["skipped"] == []
    assert len(fake_process) == 2


def test_value_error_content_is_skipped_not_retried(monkeypatch) -> None:
    def boom(
        bucket: str, key: str, version_id: str | None, *, chunk_size: int, overlap: int
    ) -> dict:
        if key.endswith(".docx"):
            raise ValueError("unsupported document type '.docx'")
        return {"document_id": "doc-abc", "num_chunks": 3}

    monkeypatch.setattr(ingest_handler, "process_document", boom)

    response = ingest_handler.lambda_handler(
        _event(
            _record(key="uploads/a.pdf", version="v1"), _record(key="uploads/b.docx", version="v1")
        ),
        None,
    )

    assert len(response["processed"]) == 1
    assert response["skipped"] == [
        {"key": "uploads/b.docx", "reason": "unsupported document type '.docx'"}
    ]
    assert response["failed"] == []


def test_transient_failure_raises_to_retry_batch(monkeypatch) -> None:
    def boom(
        bucket: str, key: str, version_id: str | None, *, chunk_size: int, overlap: int
    ) -> dict:
        raise RuntimeError("connection reset")

    monkeypatch.setattr(ingest_handler, "process_document", boom)

    with pytest.raises(RuntimeError, match="retrying batch"):
        ingest_handler.lambda_handler(_event(_record()), None)


def test_non_objectcreated_records_are_ignored(fake_process) -> None:
    response = ingest_handler.lambda_handler(_event(_record(name="ObjectRemoved:Delete")), None)

    assert response == {"processed": [], "skipped": [], "failed": []}
    assert fake_process == []


def test_multipart_completion_is_still_an_objectcreated_event(fake_process) -> None:
    """The notification's s3:ObjectCreated:* filter includes multipart
    completions; a completed multipart upload IS a new object to ingest."""
    response = ingest_handler.lambda_handler(
        _event(_record(name="ObjectCreated:CompleteMultipartUpload", key="uploads/big.pdf")),
        None,
    )

    assert len(response["processed"]) == 1
    assert response["processed"][0]["key"] == "uploads/big.pdf"


def test_record_without_s3_section_is_skipped(fake_process) -> None:
    malformed = {"eventName": "ObjectCreated:Put"}
    response = ingest_handler.lambda_handler(_event(malformed), None)

    assert response["processed"] == []
    assert response["skipped"][0]["reason"] == "record has no 's3' section"
    assert fake_process == []


def test_empty_event_is_a_noop(fake_process) -> None:
    response = ingest_handler.lambda_handler({}, None)

    assert response == {"processed": [], "skipped": [], "failed": []}
    assert fake_process == []
