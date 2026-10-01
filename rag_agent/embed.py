import json
import logging
from typing import Any

from rag_agent.bedrock import (
    DEFAULT_RETRY_ATTEMPTS,  # noqa: F401 — re-exported for existing callers
    is_retryable,  # noqa: F401 — re-exported for existing callers
    retry_call,
)
from rag_agent.ingest import PROCESSED_PREFIX

logger = logging.getLogger(__name__)

EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"
EMBEDDING_DIMENSIONS = 1024
EMBEDDING_NORMALIZE = True  # unit vectors: cosine similarity == dot product
EMBEDDED_PREFIX = "embedded/"
CHUNKS_SUFFIX = "/chunks.jsonl"
EMBEDDINGS_SUFFIX = "/embeddings.jsonl"

# Retry convention lives in rag_agent.bedrock (shared with the generation
# path); embed re-exports is_retryable/DEFAULT_RETRY_ATTEMPTS for its
# existing callers.

_client: Any = None


def _get_client() -> Any:
    """Lazily build the default Bedrock Runtime client (first call only)."""
    global _client
    if _client is None:
        import boto3

        _client = boto3.client("bedrock-runtime")
    return _client


def embed_text(
    text: str,
    *,
    model_id: str = EMBEDDING_MODEL_ID,
    dimensions: int = EMBEDDING_DIMENSIONS,
    normalize: bool = EMBEDDING_NORMALIZE,
    bedrock_client: Any | None = None,
) -> dict:

    if not isinstance(text, str) or not text:
        raise ValueError("text must be a non-empty string")

    client = bedrock_client if bedrock_client is not None else _get_client()
    body = json.dumps({"inputText": text, "dimensions": dimensions, "normalize": normalize}).encode(
        "utf-8"
    )

    response = client.invoke_model(
        body=body,
        modelId=model_id,
        accept="application/json",
        contentType="application/json",
    )
    payload = json.loads(response["body"].read())

    try:
        return {
            "embedding": payload["embedding"],
            "input_text_token_count": payload["inputTextTokenCount"],
        }
    except KeyError as exc:
        raise ValueError(f"model response missing field: {exc}") from exc


def embed_texts(
    texts: list[str],
    *,
    bedrock_client: Any | None = None,
    retry_attempts: int = DEFAULT_RETRY_ATTEMPTS,
    model_id: str = EMBEDDING_MODEL_ID,
    dimensions: int = EMBEDDING_DIMENSIONS,
    normalize: bool = EMBEDDING_NORMALIZE,
) -> list[dict]:
    if not isinstance(retry_attempts, int) or isinstance(retry_attempts, bool):
        raise ValueError("retry_attempts must be an integer")
    if retry_attempts < 1:
        raise ValueError("retry_attempts must be >= 1")

    client = bedrock_client if bedrock_client is not None else _get_client()
    if not texts:
        return []
    from functools import partial

    def embed_one(text: str) -> dict:
        return embed_text(
            text,
            model_id=model_id,
            dimensions=dimensions,
            normalize=normalize,
            bedrock_client=client,
        )

    return [
        retry_call(partial(embed_one, text), attempts=retry_attempts, label="bedrock embedding")
        for text in texts
    ]


# converts JSONL into Python dictionaries
def _parse_chunks(data: bytes) -> list[dict]:
    """Parse chunks.jsonl into the chunk records to embed."""
    chunks: list[dict] = []
    for line_number, line in enumerate(data.decode("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            chunks.append(
                {
                    "index": int(record["index"]),
                    "text": str(record["text"]),
                    "start": int(record["start"]),
                    "end": int(record["end"]),
                }
            )
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"malformed chunk record on line {line_number}: {exc}") from exc
    if not chunks:
        raise ValueError("chunks file contains no chunk records")
    return chunks


def embed_document(
    bucket: str,
    chunks_key: str,
    *,
    s3_client: Any | None = None,
    bedrock_client: Any | None = None,
    retry_attempts: int = DEFAULT_RETRY_ATTEMPTS,
) -> dict:
    if not isinstance(bucket, str) or not bucket:
        raise ValueError("bucket must be a non-empty string")
    if not isinstance(chunks_key, str) or not chunks_key.startswith(PROCESSED_PREFIX):
        raise ValueError(f"chunks_key must be '{PROCESSED_PREFIX}<document_id>/chunks.jsonl'")
    if not chunks_key.endswith(CHUNKS_SUFFIX):
        raise ValueError(f"chunks_key must end with '{CHUNKS_SUFFIX}'")
    document_id = chunks_key[len(PROCESSED_PREFIX) : -len(CHUNKS_SUFFIX)]
    if not document_id or "/" in document_id:
        raise ValueError(f"chunks_key must be '{PROCESSED_PREFIX}<document_id>/chunks.jsonl'")

    s3 = s3_client if s3_client is not None else _get_s3_client()
    bedrock = bedrock_client if bedrock_client is not None else _get_client()

    response = s3.get_object(Bucket=bucket, Key=chunks_key)
    chunks = _parse_chunks(response["Body"].read())

    embedded_key = f"{EMBEDDED_PREFIX}{document_id}{EMBEDDINGS_SUFFIX}"
    records: list[dict] = []
    for chunk in chunks:
        result = embed_texts(
            [chunk["text"]],
            bedrock_client=bedrock,
            retry_attempts=retry_attempts,
        )[0]
        records.append(
            {
                "index": chunk["index"],
                "text": chunk["text"],
                "embedding": result["embedding"],
                "input_text_token_count": result["input_text_token_count"],
                "start": chunk["start"],
                "end": chunk["end"],
            }
        )

    body = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records).encode(
        "utf-8"
    )
    s3.put_object(Bucket=bucket, Key=embedded_key, Body=body, ContentType="application/json")

    logger.info("embedded %d chunks from %s -> %s", len(records), chunks_key, embedded_key)
    return {
        "document_id": document_id,
        "model_id": EMBEDDING_MODEL_ID,
        "dimensions": EMBEDDING_DIMENSIONS,
        "num_embeddings": len(records),
        "keys_written": [embedded_key],
    }


_s3_client: Any = None


def _get_s3_client() -> Any:
    """Lazily build the default S3 client (first call only)."""
    global _s3_client
    if _s3_client is None:
        import boto3

        _s3_client = boto3.client("s3")
    return _s3_client
