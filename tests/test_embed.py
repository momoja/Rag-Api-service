"""Tests for rag_agent.embed — offline: fake Bedrock + S3 clients.

No network, no credentials. Bedrock responses are synthesized in-memory;
retry/backoff sleeps are patched out so the suite stays fast.
"""

import io
import json

import pytest
from botocore.exceptions import ClientError

import rag_agent.bedrock as bedrock_mod
from rag_agent.embed import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL_ID,
    embed_document,
    embed_text,
    embed_texts,
    is_retryable,
)

BUCKET = "rag-agent-dev-documents-000000000000"


def client_error(code: str, message: str = "boom") -> ClientError:
    return ClientError(
        error_response={"Error": {"Code": code, "Message": message}},
        operation_name="InvokeModel",
    )


class FakeBedrock:
    """Serves canned embeddings; optionally fails the next N calls."""

    def __init__(
        self, *, errors: list[Exception] | None = None, dims: int = EMBEDDING_DIMENSIONS
    ) -> None:
        self.errors = list(errors or [])
        self.dims = dims
        self.calls: list[dict] = []

    def invoke_model(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        if self.errors:
            raise self.errors.pop(0)
        body = json.loads(kwargs["body"])
        text = body["inputText"]
        payload = {
            "embedding": [float(len(text)) + 0.1] * int(body["dimensions"]),
            "inputTextTokenCount": 7,
        }
        return {"body": io.BytesIO(json.dumps(payload).encode("utf-8"))}

    def embedding_of(self, text: str) -> list[float]:
        return [float(len(text)) + 0.1] * self.dims


class FakeS3:
    """In-memory S3 keyed by object; serves canned bodies, records writes."""

    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = dict(objects or {})

    def get_object(self, Bucket: str, Key: str) -> dict:
        assert Bucket == BUCKET
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, Bucket: str, Key: str, Body: bytes, ContentType: str) -> None:
        assert Bucket == BUCKET
        assert ContentType == "application/json"
        self.objects[Key] = bytes(Body)


def chunks_jsonl(*records: dict) -> bytes:
    return "".join(json.dumps(r) + "\n" for r in records).encode("utf-8")


# ---------------------------------------------------------------------------
# embed_text
# ---------------------------------------------------------------------------


def test_embed_text_happy_path() -> None:
    bedrock = FakeBedrock()
    result = embed_text("hello world", bedrock_client=bedrock)

    assert result["embedding"] == bedrock.embedding_of("hello world")
    assert result["input_text_token_count"] == 7
    call = bedrock.calls[0]
    assert call["modelId"] == EMBEDDING_MODEL_ID
    assert call["accept"] == "application/json"
    assert call["contentType"] == "application/json"
    body = json.loads(call["body"])
    assert body == {"inputText": "hello world", "dimensions": 1024, "normalize": True}


def test_embed_text_rejects_empty_text() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        embed_text("", bedrock_client=FakeBedrock())


def test_embed_text_surfaces_model_response_missing_field() -> None:
    class TruncatedBedrock(FakeBedrock):
        def invoke_model(self, **kwargs) -> dict:
            self.calls.append(kwargs)
            return {"body": io.BytesIO(b'{"inputTextTokenCount": 1}')}

    with pytest.raises(ValueError, match="missing field"):
        embed_text("x", bedrock_client=TruncatedBedrock())


# ---------------------------------------------------------------------------
# retry classification and embed_texts
# ---------------------------------------------------------------------------


def test_is_retryable_only_for_transient_codes() -> None:
    assert is_retryable(client_error("ThrottlingException"))
    assert is_retryable(client_error("ModelTimeoutException"))
    assert is_retryable(client_error("ServiceUnavailableException"))
    assert not is_retryable(client_error("AccessDeniedException"))
    assert not is_retryable(client_error("ValidationException"))
    assert not is_retryable(ValueError("nope"))


def test_embed_texts_retries_transient_then_succeeds(monkeypatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr(bedrock_mod.time, "sleep", slept.append)
    bedrock = FakeBedrock(
        errors=[client_error("ThrottlingException"), client_error("ModelTimeoutException")]
    )

    results = embed_texts(["one", "two"], bedrock_client=bedrock, retry_attempts=3)

    assert len(results) == 2
    assert len(bedrock.calls) == 4  # 2 failures + 2 successes, sequential
    assert len(slept) == 2
    assert slept[0] < slept[1]  # exponential backoff grows


def test_embed_texts_raises_after_retries_exhausted(monkeypatch) -> None:
    monkeypatch.setattr(bedrock_mod.time, "sleep", lambda s: None)
    bedrock = FakeBedrock(errors=[client_error("ThrottlingException")] * 5)

    with pytest.raises(ClientError, match="Throttling"):
        embed_texts(["x"], bedrock_client=bedrock, retry_attempts=2)

    assert len(bedrock.calls) == 2  # bounded: never more than retry_attempts


def test_embed_texts_does_not_retry_permanent_errors(monkeypatch) -> None:
    monkeypatch.setattr(bedrock_mod.time, "sleep", lambda s: None)
    bedrock = FakeBedrock(errors=[client_error("AccessDeniedException")])

    with pytest.raises(ClientError, match="AccessDenied"):
        embed_texts(["x"], bedrock_client=bedrock, retry_attempts=3)

    assert len(bedrock.calls) == 1  # config errors fail fast, never retried


def test_embed_texts_does_not_retry_value_errors(monkeypatch) -> None:
    monkeypatch.setattr(bedrock_mod.time, "sleep", lambda s: None)

    class RejectingBedrock(FakeBedrock):
        def invoke_model(self, **kwargs) -> dict:
            self.calls.append(kwargs)
            raise ValueError("model rejects this text")

    bedrock = RejectingBedrock()
    with pytest.raises(ValueError, match="rejects"):
        embed_texts(["x"], bedrock_client=bedrock, retry_attempts=3)

    assert len(bedrock.calls) == 1  # content errors fail fast, never retried


def test_embed_texts_empty_and_bad_retry_arguments() -> None:
    assert embed_texts([], bedrock_client=FakeBedrock()) == []
    for bad in (0, -1, True, 1.5):
        with pytest.raises(ValueError):
            embed_texts(["x"], bedrock_client=FakeBedrock(), retry_attempts=bad)


# ---------------------------------------------------------------------------
# embed_document
# ---------------------------------------------------------------------------


def test_embed_document_happy_path() -> None:
    chunks = chunks_jsonl(
        {"index": 0, "text": "alpha", "start": 0, "end": 5},
        {"index": 1, "text": "beta beta", "start": 6, "end": 15},
    )
    s3 = FakeS3({"processed/doc123/chunks.jsonl": chunks})
    bedrock = FakeBedrock()

    result = embed_document(
        BUCKET, "processed/doc123/chunks.jsonl", s3_client=s3, bedrock_client=bedrock
    )

    assert result["document_id"] == "doc123"
    assert result["model_id"] == EMBEDDING_MODEL_ID
    assert result["dimensions"] == 1024
    assert result["num_embeddings"] == 2
    assert result["keys_written"] == ["embedded/doc123/embeddings.jsonl"]

    lines = [
        json.loads(line)
        for line in s3.objects["embedded/doc123/embeddings.jsonl"].decode().splitlines()
    ]
    assert lines == [
        {
            "index": 0,
            "text": "alpha",
            "embedding": bedrock.embedding_of("alpha"),
            "input_text_token_count": 7,
            "start": 0,
            "end": 5,
        },
        {
            "index": 1,
            "text": "beta beta",
            "embedding": bedrock.embedding_of("beta beta"),
            "input_text_token_count": 7,
            "start": 6,
            "end": 15,
        },
    ]
    assert len(lines[0]["embedding"]) == EMBEDDING_DIMENSIONS


def test_embed_document_is_idempotent() -> None:
    chunks = chunks_jsonl({"index": 0, "text": "alpha", "start": 0, "end": 5})
    s3 = FakeS3({"processed/doc123/chunks.jsonl": chunks})
    bedrock = FakeBedrock()

    first = embed_document(
        BUCKET, "processed/doc123/chunks.jsonl", s3_client=s3, bedrock_client=bedrock
    )
    second = embed_document(
        BUCKET, "processed/doc123/chunks.jsonl", s3_client=s3, bedrock_client=bedrock
    )

    assert second["keys_written"] == first["keys_written"]
    assert set(s3.objects) == {"processed/doc123/chunks.jsonl", "embedded/doc123/embeddings.jsonl"}


@pytest.mark.parametrize(
    "bad_key",
    [
        "processed/doc123",
        "uploads/doc123/chunks.jsonl",
        "processed/a/b/chunks.jsonl",
        "doc123/chunks.jsonl",
    ],
    ids=["no-suffix", "wrong-prefix", "nested-id", "no-prefix"],
)
def test_embed_document_rejects_bad_chunks_keys(bad_key: str) -> None:
    with pytest.raises(ValueError, match="chunks_key"):
        embed_document(BUCKET, bad_key, s3_client=FakeS3(), bedrock_client=FakeBedrock())


def test_embed_document_rejects_malformed_chunk_records() -> None:
    malformed = b'{"index": 0, "text": "a", "start": 0, "end": 1}\n' + b"not json\n"
    s3 = FakeS3({"processed/doc123/chunks.jsonl": malformed})
    with pytest.raises(ValueError, match="malformed chunk record on line 2"):
        embed_document(
            BUCKET, "processed/doc123/chunks.jsonl", s3_client=s3, bedrock_client=FakeBedrock()
        )
    assert "embedded/doc123/embeddings.jsonl" not in s3.objects


def test_embed_document_rejects_empty_chunks_file() -> None:
    s3 = FakeS3({"processed/doc123/chunks.jsonl": b"\n\n"})
    with pytest.raises(ValueError, match="no chunk records"):
        embed_document(
            BUCKET, "processed/doc123/chunks.jsonl", s3_client=s3, bedrock_client=FakeBedrock()
        )
    assert "embedded/doc123/embeddings.jsonl" not in s3.objects


def test_embed_document_never_stages_partial_output_on_transient_failure(monkeypatch) -> None:
    monkeypatch.setattr(bedrock_mod.time, "sleep", lambda s: None)
    chunks = chunks_jsonl(
        {"index": 0, "text": "alpha", "start": 0, "end": 5},
        {"index": 1, "text": "beta", "start": 6, "end": 10},
    )
    s3 = FakeS3({"processed/doc123/chunks.jsonl": chunks})
    # First chunk succeeds, second chunk always throttles past its retries.
    bedrock = FakeBedrock(errors=[client_error("ThrottlingException")] * 10)

    with pytest.raises(ClientError, match="Throttling"):
        embed_document(
            BUCKET,
            "processed/doc123/chunks.jsonl",
            s3_client=s3,
            bedrock_client=bedrock,
            retry_attempts=2,
        )

    assert "embedded/doc123/embeddings.jsonl" not in s3.objects  # no partial write
