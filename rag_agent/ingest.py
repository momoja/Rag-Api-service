"""Chapter 6 document-ingestion core: extract -> clean -> chunk -> stage.

The pipeline consumes objects uploaded to ``<bucket>/uploads/`` (minted by
the Chapter 5 presign API) and stages ready-to-embed output under
``<bucket>/processed/<document_id>/`` in the same bucket:
``chunks.jsonl`` (one JSON object per chunk) and ``metadata.json``.

Stage boundaries, deliberately:
- Extraction supports PDF (pypdf) and UTF-8 plain text (.txt/.md). Other
  formats are rejected with ValueError — permanent content problems raise
  ValueError (handler skips, no retry); anything else is a transient failure
  (S3 retries the invocation).
- Cleaning normalizes Unicode (NFKC), unifies newlines, drops control
  characters, strips per-line trailing whitespace, and collapses blank-line
  runs. It does NOT rejoin hyphenated PDF line breaks or rewrite whitespace
  inside lines — paragraph structure survives for .md, and embedding
  handles stray spacing.
- Chunking is a character window with overlap, broken at whitespace when
  the window lands mid-word (>= 60% of the window kept, else hard cut).
  Deterministic and pure: same text in, same chunks out.

document_id is a SHA-256 of (bucket, key, version_id): re-processing the
same object version overwrites the same processed/ keys (idempotent), while
a new upload version stages alongside the old one.

Design mirrors rag_agent.storage: SDK clients are injectable, so this module
is fully testable offline; Lambda handlers only wrap it with event/env glue.
"""

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
# (ch7); 200 chars of overlap (~15%) preserves boundary context.
DEFAULT_CHUNK_SIZE = 1200
DEFAULT_CHUNK_OVERLAP = 200

SUPPORTED_TEXT_EXTENSIONS = (".txt", ".md")

_client: Any = None


@dataclass(frozen=True)
class Chunk:
    """A single chunk of cleaned document text.

    ``start``/``end`` are character offsets into the cleaned text that was
    chunked, so chunks can be traced back to the exact source slice.
    """

    index: int
    text: str
    start: int
    end: int


def _extension_of(source_key: str) -> str:
    return os.path.splitext(source_key)[1].lower()


def extract_text(data: bytes, *, source_key: str) -> str:
    """Extract raw text from ``data`` according to ``source_key``'s extension.

    Raises ValueError for unsupported formats, corrupt documents, and
    non-UTF-8 text — all permanent conditions the caller should skip, not
    retry.
    """
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
    """Normalize extracted text for chunking. Pure and deterministic.

    Steps: NFKC Unicode normalization; CRLF/CR -> LF; drop control
    characters (keeping newline/tab); strip per-line trailing whitespace;
    collapse runs of 3+ newlines to 2 (at most one blank line between
    paragraphs); strip surrounding whitespace. Intra-line spacing and
    newlines are otherwise preserved (markdown structure survives).
    """
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(c for c in text if c in "\n\t" or (31 < ord(c) < 127 or ord(c) > 127))
    lines = [line.rstrip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _last_whitespace(text: str, start: int, end: int) -> int | None:
    """Index of the last whitespace char in ``text[start:end]``, or None."""
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
    """Split ``text`` into overlapping character windows. Pure/deterministic.

    Windows never start on whitespace. When a full window ends mid-word it
    retreats to the last whitespace boundary — unless that would leave less
    than 60% of the window, in which case it hard-cuts (unbroken long
    tokens must still make progress). Consecutive chunks overlap by
    ``overlap`` characters, measured between chunk boundaries.
    """
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
    """Deterministic id for one object version (idempotency key)."""
    digest = hashlib.sha256()
    digest.update(bucket.encode("utf-8"))
    digest.update(b"\0")
    digest.update(key.encode("utf-8"))
    digest.update(b"\0")
    digest.update((version_id or "").encode("utf-8"))
    return digest.hexdigest()[:32]


def _get_client() -> Any:
    """Lazily build the default S3 client (first call only)."""
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
    """Run the full pipeline on one S3 object and stage its output.

    Downloads the object, extracts/cleans/chunks it, and writes
    ``processed/<doc_id>/chunks.jsonl`` + ``metadata.json`` (PutObject
    overwrites, so re-processing the same version is idempotent).

    Raises ValueError on permanent content problems (unsupported format,
    corrupt PDF, non-UTF-8, no extractable text). Returns a summary dict on
    success.
    """
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
