"""Chapter 7 embedding core: embed Chapter 6's chunk output with Amazon
Bedrock Titan, staged for the (Chapter 8) vector store.

Why embeddings: RAG retrieval must find the chunks *relevant* to a question,
not just the ones sharing literal keywords. An embedding model maps text to a
fixed-dimension vector such that semantically similar text lands closer
together; cosine distance over those vectors is the relevance ranking the
(Chapter 9) retriever will use. Chunk-level vectors are computed at ingest
time so retrieval stays one fast vector lookup plus a Bedrock query-embed
call — never a scan of raw text.

Model/config contract (decision 5): Amazon Titan Text Embeddings V2
(``amazon.titan-embed-text-v2:0``), 1024 dimensions, normalized output
(cosine == dot product). The dimension is a hard contract the Chapter 8
vector-store index must honor. Input limit is 8192 tokens; the Chapter 6
chunker (1200 chars, ~300 tokens) keeps every chunk far below it.

Layout: reads ``processed/<document_id>/chunks.jsonl`` (written by Chapter
6's ingest-document Lambda) and writes
``embedded/<document_id>/embeddings.jsonl`` — one JSON object per line:
``{index, text, embedding, input_text_token_count, start, end}``. ``text``
travels with the vector so the Chapter 9 retriever can return context
without a second read. Keys derive from the source path, so re-running a
document overwrites its embeddings (idempotent).

Error/retry strategy: Bedrock calls are individually retried with bounded
exponential backoff (default 3 attempts) for transient failures only
(throttling, model timeout/error, service unavailability, internal server,
network). Validation failures (bad input text, wrong parameters) are
permanent — raised as ValueError so the Lambda skips the document instead
of retrying forever. Misconfiguration (access denied, missing model) is
also permanent but deliberately NOT ValueError: it keeps failing loudly
until a human fixes the deployment.
"""

import json
import logging
import time
from typing import Any

from rag_agent.ingest import PROCESSED_PREFIX

logger = logging.getLogger(__name__)

EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"
EMBEDDING_DIMENSIONS = 1024
EMBEDDING_NORMALIZE = True  # unit vectors: cosine similarity == dot product
EMBEDDED_PREFIX = "embedded/"
CHUNKS_SUFFIX = "/chunks.jsonl"

DEFAULT_RETRY_ATTEMPTS = 3
_MAX_RETRY_DELAY_SECONDS = 8.0

# Codes boto3 surfaces as ClientError; each is transient and worth a retry.
RETRYABLE_ERROR_CODES = frozenset(
    {
        "ThrottlingException",
        "ModelTimeoutException",
        "ModelErrorException",
        "InternalServerException",
        "ServiceUnavailableException",
    }
)

_client: Any = None


def _get_client() -> Any:
    """Lazily build the default Bedrock Runtime client (first call only)."""
    global _client
    if _client is None:
        import boto3

        _client = boto3.client("bedrock-runtime")
    return _client


def is_retryable(exc: Exception) -> bool:
    """True when ``exc`` is a transient Bedrock/network failure."""
    from botocore.exceptions import ClientError

    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        return code in RETRYABLE_ERROR_CODES
    return False


def embed_text(
    text: str,
    *,
    model_id: str = EMBEDDING_MODEL_ID,
    dimensions: int = EMBEDDING_DIMENSIONS,
    normalize: bool = EMBEDDING_NORMALIZE,
    bedrock_client: Any | None = None,
) -> dict:
    """Embed one text via Bedrock Titan and return the model response.

    Returns ``{"embedding": [...], "input_text_token_count": N}``.

    Raises ValueError for permanent input problems (model rejects the text);
    transient Bedrock failures propagate untouched so callers can retry.
    """
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
    """Embed each text with bounded retry on transient failures.

    A text that exhausts its retries raises the last error — the caller
    (Lambda) lets S3 retry the whole document, which is idempotent.
    """
    if not isinstance(retry_attempts, int) or isinstance(retry_attempts, bool):
        raise ValueError("retry_attempts must be an integer")
    if retry_attempts < 1:
        raise ValueError("retry_attempts must be >= 1")

    client = bedrock_client if bedrock_client is not None else _get_client()
    if not texts:
        return []
    results: list[dict] = []
    for text in texts:
        for attempt in range(retry_attempts):
            try:
                result = embed_text(
                    text,
                    model_id=model_id,
                    dimensions=dimensions,
                    normalize=normalize,
                    bedrock_client=client,
                )
                results.append(result)
                break
            except Exception as exc:
                # Transient Bedrock failures (classified by is_retryable) get
                # bounded exponential backoff; everything else is permanent
                # and surfaces immediately. Sleep is patched in offline tests.
                if not is_retryable(exc) or attempt == retry_attempts - 1:
                    raise
                delay = min(0.5 * (2**attempt), _MAX_RETRY_DELAY_SECONDS)
                logger.warning(
                    "bedrock retry %d/%d after %s in %.1fs",
                    attempt + 1,
                    retry_attempts,
                    type(exc).__name__,
                    delay,
                )
                time.sleep(delay)
    return results


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
    """Embed one document's chunks and stage ``embedded/<doc_id>/embeddings.jsonl``.

    ``chunks_key`` must be ``processed/<document_id>/chunks.jsonl``; the
    document_id is derived from the path, so re-running the same source
    overwrites the same embeddings key (idempotent).

    Raises ValueError on permanent content problems (bad path, malformed
    chunk records, model input rejection). Returns a summary dict.
    """
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

    embedded_key = f"{EMBEDDED_PREFIX}{document_id}/embeddings.jsonl"
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
