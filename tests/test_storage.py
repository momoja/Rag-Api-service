"""Tests for rag_agent.storage — offline, no AWS calls (fake S3 client)."""

import pytest

import rag_agent.storage as storage
from rag_agent.storage import (
    DEFAULT_EXPIRES_SECONDS,
    MAX_EXPIRES_SECONDS,
    MIN_EXPIRES_SECONDS,
    presign_upload_url,
    validate_key,
)

BUCKET = "rag-agent-dev-documents-000000000000"
FAKE_URL = "https://example.invalid/presigned"


class FakeS3Client:
    """Records the presign call; no network involvement."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, int]] = []

    def generate_presigned_url(self, ClientMethod: str, Params: dict, ExpiresIn: int) -> str:
        self.calls.append((ClientMethod, Params, ExpiresIn))
        return FAKE_URL


@pytest.fixture
def fake_s3() -> FakeS3Client:
    return FakeS3Client()


def test_presign_happy_path(fake_s3: FakeS3Client) -> None:
    result = presign_upload_url(BUCKET, "docs/report.pdf", s3_client=fake_s3)

    assert result["url"] == FAKE_URL
    assert result["method"] == "PUT"
    assert result["bucket"] == BUCKET
    assert result["key"] == "docs/report.pdf"
    assert result["expires_in"] == DEFAULT_EXPIRES_SECONDS
    assert fake_s3.calls == [
        ("put_object", {"Bucket": BUCKET, "Key": "docs/report.pdf"}, DEFAULT_EXPIRES_SECONDS)
    ]


def test_presign_custom_expiry(fake_s3: FakeS3Client) -> None:
    result = presign_upload_url(BUCKET, "a.pdf", expires_in=120, s3_client=fake_s3)
    assert result["expires_in"] == 120
    assert fake_s3.calls[0][2] == 120


@pytest.mark.parametrize("bad", [MIN_EXPIRES_SECONDS - 1, MAX_EXPIRES_SECONDS + 1])
def test_presign_rejects_expiry_out_of_bounds(fake_s3: FakeS3Client, bad: int) -> None:
    with pytest.raises(ValueError, match="expires_in"):
        presign_upload_url(BUCKET, "a.pdf", expires_in=bad, s3_client=fake_s3)


@pytest.mark.parametrize(
    "bad_key",
    [
        "",
        "/absolute/path.pdf",
        "seg/../escape.pdf",
        "seg/..",
        "x" * (1024 + 1),
        None,
        42,
    ],
)
def test_presign_rejects_invalid_keys(fake_s3: FakeS3Client, bad_key) -> None:
    with pytest.raises(ValueError):
        presign_upload_url(BUCKET, bad_key, s3_client=fake_s3)


@pytest.mark.parametrize("bad_bucket", ["", None, 7])
def test_presign_rejects_invalid_bucket(fake_s3: FakeS3Client, bad_bucket) -> None:
    with pytest.raises(ValueError, match="bucket"):
        presign_upload_url(bad_bucket, "a.pdf", s3_client=fake_s3)


@pytest.mark.parametrize(
    ("key", "ok"),
    [
        ("report.pdf", True),
        ("docs/sub/report.pdf", True),
        ("with space + plus.pdf", True),
        ("..", False),
        ("..", False),
    ],
)
def test_validate_key_examples(key: str, ok: bool) -> None:
    if ok:
        assert validate_key(key) == key
    else:
        with pytest.raises(ValueError):
            validate_key(key)


def test_default_client_forces_sigv4() -> None:
    """Presigned URLs must be SigV4 (X-Amz-*): SigV2 is deprecated and
    refused for presigned requests on new buckets."""
    storage._client = None
    client = storage._get_client()
    assert client.meta.config.signature_version == "s3v4"
