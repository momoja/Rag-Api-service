"""S3 document storage helpers for the self-managed RAG system.

The raw-documents S3 bucket (infra/dev/s3.tf) is the ingestion entry point:
clients upload via pre-signed URLs, Chapter 6's pipeline reads the objects.

Design: SDK clients are injectable so this module is fully testable offline
with a fake client; Lambda handlers (lambda/) wrap these functions with
event/env glue and never contain business logic.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

MIN_EXPIRES_SECONDS = 60
DEFAULT_EXPIRES_SECONDS = 900
MAX_EXPIRES_SECONDS = 3600

MAX_KEY_LENGTH = 1024

_client: Any = None


def validate_key(key: str) -> str:
    """Validate an S3 object key for uploads. Returns the key unchanged.

    Raises ValueError with a client-facing message on invalid keys.
    """
    if not isinstance(key, str):
        raise ValueError("key must be a string")
    if not key:
        raise ValueError("key must not be empty")
    if len(key) > MAX_KEY_LENGTH:
        raise ValueError(f"key exceeds {MAX_KEY_LENGTH} characters")
    if key.startswith("/"):
        raise ValueError("key must not start with '/' (absolute paths unsupported)")
    if ".." in key.split("/"):
        raise ValueError("key must not contain '..' path segments")
    return key


def _get_client() -> Any:
    """Lazily build the default S3 client (first call only)."""
    global _client
    if _client is None:
        import boto3
        from botocore.config import Config

        # Force SigV4: on the us-east-1 global endpoint botocore falls back to
        # deprecated SigV2 (AWSAccessKeyId=...), which AWS no longer accepts
        # for presigned requests to new buckets. X-Amz-* query params = v4.
        _client = boto3.client("s3", config=Config(signature_version="s3v4"))
    return _client


def presign_upload_url(
    bucket: str,
    key: str,
    *,
    expires_in: int = DEFAULT_EXPIRES_SECONDS,
    s3_client: Any | None = None,
) -> dict[str, str | int]:
    """Return a pre-signed PUT URL for uploading ``key`` to ``bucket``.

    URL generation is client-side signing — no AWS call is made — which is
    why the function is safe to run and test without credentials.

    Returns a response-contract dict (method/bucket/key/url/expires_in).
    """
    if not isinstance(bucket, str) or not bucket:
        raise ValueError("bucket must be a non-empty string")
    if not MIN_EXPIRES_SECONDS <= expires_in <= MAX_EXPIRES_SECONDS:
        raise ValueError(
            f"expires_in must be between {MIN_EXPIRES_SECONDS} and {MAX_EXPIRES_SECONDS} seconds"
        )
    validate_key(key)

    client = s3_client if s3_client is not None else _get_client()
    url = client.generate_presigned_url(
        ClientMethod="put_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expires_in,
    )
    logger.info("pre-signed PUT url for s3://%s/%s (%ss)", bucket, key, expires_in)
    return {
        "method": "PUT",
        "bucket": bucket,
        "key": key,
        "url": url,
        "expires_in": expires_in,
    }
