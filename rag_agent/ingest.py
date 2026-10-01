import hashlib
import io
import json
import logging
import os
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from rag_agent.storage import UPLOADS_PREFIX

logger = logging.getLogger(__name__)

PROCESSED_PREFIX = "processed/"

# ~1200 chars is a conservative window for a 1024-token embedding model
DEFAULT_CHUNK_SIZE = 1200
DEFAULT_CHUNK_OVERLAP = 200

SUPPORTED_TEXT_EXTENSIONS = (".txt", ".md")

_client: Any = None


@dataclass(frozen=True)
class Chunk:
    index: int
    text: str
    start: int
    end: int


def _extension_of(source_key: str) -> str:
    return os.path.splitext(source_key)[1].lower()


def extract_text(data: bytes, *, source_key: str) -> str:

    ext = _extension_of(source_key)
    if ext == ".pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            pages = [page.extract_text() or "" for page in reader.pages]
        except Exception as exc:
            # Any failure inside pypdf is a content problem (corrupt or
            # encrypted PDF), not a transient error.
            raise ValueError(f"failed to extract PDF text: {exc}") from exc
        return "\n".join(pages)
    if ext in SUPPORTED_TEXT_EXTENSIONS:
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"{source_key} is not valid UTF-8 text") from exc
    if ext == "":
        raise ValueError(f"{source_key} has no extension; supported: .pdf, .txt, .md")
    raise ValueError(f"unsupported document type '{ext}'; supported: .pdf, .txt, .md")


def clean_text(text: str) -> str:

    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(c for c in text if c in "\n\t" or (31 < ord(c) < 127 or ord(c) > 127))
    lines = [line.rstrip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _last_whitespace(text: str, start: int, end: int) -> int | None:

    for i in range(end - 1, start - 1, -1):
        if text[i].isspace():
            return i
    return None


def chunk_text(
    text: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[Chunk]:

    if not isinstance(chunk_size, int) or isinstance(chunk_size, bool):
        raise ValueError("chunk_size must be an integer")
    if chunk_size < 1:
        raise ValueError("chunk_size must be >= 1")
    if not isinstance(overlap, int) or isinstance(overlap, bool):
        raise ValueError("overlap must be an integer")
    if not 0 <= overlap < chunk_size:
        raise ValueError("overlap must be in [0, chunk_size)")

    chunks: list[Chunk] = []
    pos = 0
    index = 0
    n = len(text)
    while pos < n:
        while pos < n and text[pos].isspace():
            pos += 1
        if pos >= n:
            break

        end = min(pos + chunk_size, n)
        if end < n:
            cut = _last_whitespace(text, pos, end)
            # Only shorten at a boundary when it keeps >= 60% of the window;
            # otherwise the "word" is longer than the window and must be cut.
            if cut is not None and cut - pos >= (chunk_size * 3) // 5:
                end = cut

        chunks.append(Chunk(index=index, text=text[pos:end], start=pos, end=end))
        index += 1
        if end == n:
            break
        pos = end - overlap
    return chunks


def document_id(bucket: str, key: str, version_id: str | None) -> str:

    digest = hashlib.sha256()
    digest.update(bucket.encode("utf-8"))
    digest.update(b"\0")
    digest.update(key.encode("utf-8"))
    digest.update(b"\0")
    digest.update((version_id or "").encode("utf-8"))
    return digest.hexdigest()[:32]


def _get_client() -> Any:

    global _client
    if _client is None:
        import boto3

        _client = boto3.client("s3")
    return _client


def _processed_keys(bucket: str, doc_id: str) -> tuple[str, str]:
    base = f"{PROCESSED_PREFIX}{doc_id}"
    return f"{base}/chunks.jsonl", f"{base}/metadata.json"


def process_document(
    bucket: str,
    key: str,
    version_id: str | None,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
    s3_client: Any | None = None,
) -> dict:

    if not isinstance(bucket, str) or not bucket:
        raise ValueError("bucket must be a non-empty string")
    if not isinstance(key, str) or not key:
        raise ValueError("key must be a non-empty string")
    if not key.startswith(UPLOADS_PREFIX):
        raise ValueError(f"key must start with '{UPLOADS_PREFIX}'")

    client = s3_client if s3_client is not None else _get_client()

    response = client.get_object(Bucket=bucket, Key=key)
    data = response["Body"].read()
    if not isinstance(data, bytes):
        data = bytes(data)

    content_type = response.get("ContentType") or "application/octet-stream"
    text = extract_text(data, source_key=key)
    cleaned = clean_text(text)
    if not cleaned:
        raise ValueError(f"{key} contains no extractable text")
    chunks = chunk_text(cleaned, chunk_size=chunk_size, overlap=overlap)
    if not chunks:
        raise ValueError(f"{key} produced no chunks")

    doc_id = document_id(bucket, key, version_id)
    chunks_key, metadata_key = _processed_keys(bucket, doc_id)

    chunks_body = "".join(
        json.dumps(asdict(chunk), ensure_ascii=False) + "\n" for chunk in chunks
    ).encode("utf-8")
    metadata_body = json.dumps(
        {
            "document_id": doc_id,
            "source": {
                "bucket": bucket,
                "key": key,
                "version_id": version_id,
                "size_bytes": len(data),
                "content_type": content_type,
            },
            "ingested_at": datetime.now(UTC).isoformat(),
            "pipeline": {
                "extractor": "pypdf" if _extension_of(key) == ".pdf" else "utf-8-text",
                "chunker": "char-window",
            },
            "chunking": {"chunk_size": chunk_size, "overlap": overlap},
            "cleaned_characters": len(cleaned),
            "num_chunks": len(chunks),
        },
        ensure_ascii=False,
    ).encode("utf-8")

    client.put_object(
        Bucket=bucket, Key=chunks_key, Body=chunks_body, ContentType="application/json"
    )
    client.put_object(
        Bucket=bucket, Key=metadata_key, Body=metadata_body, ContentType="application/json"
    )
    logger.info(
        "ingested s3://%s/%s (v%s) -> %s (%d chunks)",
        bucket,
        key,
        version_id,
        chunks_key,
        len(chunks),
    )
    return {
        "document_id": doc_id,
        "source": {"bucket": bucket, "key": key, "version_id": version_id},
        "num_chunks": len(chunks),
        "cleaned_characters": len(cleaned),
        "keys_written": [chunks_key, metadata_key],
    }
