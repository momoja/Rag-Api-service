import logging
from typing import Any

logger = logging.getLogger(__name__)

MIN_EXPIRES_SECONDS = 60
DEFAULT_EXPIRES_SECONDS = 900
MAX_EXPIRES_SECONDS = 3600

MAX_KEY_LENGTH = 1024

UPLOADS_PREFIX = "uploads/"

_client: Any = None


def validate_key(key: str) -> str:
    
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


def validate_upload_key(key: str) -> str:
    
    validate_key(key)
    if not key.startswith(UPLOADS_PREFIX):
        raise ValueError(f"key must start with '{UPLOADS_PREFIX}'")
    return key


def _get_client() -> Any:
    
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
    
    if not isinstance(bucket, str) or not bucket:
        raise ValueError("bucket must be a non-empty string")
    if not MIN_EXPIRES_SECONDS <= expires_in <= MAX_EXPIRES_SECONDS:
        raise ValueError(
            f"expires_in must be between {MIN_EXPIRES_SECONDS} and {MAX_EXPIRES_SECONDS} seconds"
        )
    validate_upload_key(key)

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
