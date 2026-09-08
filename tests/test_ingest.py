"""Tests for rag_agent.ingest — offline: fake S3 client, pypdf-built PDFs.

No network, no credentials. PDF fixtures are synthesized in-memory with
pypdf itself (the runtime dependency), so no binary files live in the repo.
"""

import io
import json

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from rag_agent.ingest import (
    PROCESSED_PREFIX,
    Chunk,
    chunk_text,
    clean_text,
    document_id,
    extract_text,
    process_document,
)

BUCKET = "rag-agent-dev-documents-000000000000"


def make_pdf(*page_texts: str) -> bytes:
    """Build a minimal single/multi-page PDF whose pages contain exactly the
    given text lines, using only pypdf (no fixtures, no extra dependencies).
    """
    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)
    for line in page_texts:
        page = writer.add_blank_page(width=612, height=792)
        fonts = DictionaryObject({NameObject("/F1"): font_ref})
        resources = DictionaryObject({NameObject("/Font"): fonts})
        page[NameObject("/Resources")] = resources
        content = DecodedStreamObject()
        content.set_data(f"BT /F1 12 Tf 72 720 Td ({line}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(content)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_make_pdf_is_extractable() -> None:
    """Guard the fixture builder itself: the PDFs must round-trip text."""
    data = make_pdf("Hello PDF world")
    reader = PdfReader(io.BytesIO(data))
    assert len(reader.pages) == 1
    assert "Hello PDF world" in (reader.pages[0].extract_text() or "")


# ---------------------------------------------------------------------------
# extract_text
# ---------------------------------------------------------------------------


def test_extract_pdf_single_and_multi_page() -> None:
    data = make_pdf("First page content")
    assert "First page content" in extract_text(data, source_key="uploads/a.pdf")

    data2 = make_pdf("First page content", "Second page content")
    extracted = extract_text(data2, source_key="uploads/a.pdf")
    assert extracted.index("First page content") < extracted.index("Second page content")


def test_extract_plain_text_and_markdown() -> None:
    for name in ("uploads/notes.txt", "uploads/notes.md"):
        assert extract_text(b"line one\nline two", source_key=name) == "line one\nline two"


def test_extract_rejects_invalid_utf8_text() -> None:
    with pytest.raises(ValueError, match="not valid UTF-8"):
        extract_text(b"\xff\xfe not text", source_key="uploads/a.txt")


@pytest.mark.parametrize("name", ["uploads/a.docx", "uploads/a.html"])
def test_extract_rejects_unsupported_types(name: str) -> None:
    with pytest.raises(ValueError, match="unsupported document type"):
        extract_text(b"whatever", source_key=name)


def test_extract_rejects_extensionless_key() -> None:
    with pytest.raises(ValueError, match="no extension"):
        extract_text(b"whatever", source_key="uploads/README")


def test_extract_wraps_corrupt_pdf() -> None:
    with pytest.raises(ValueError, match="failed to extract PDF text"):
        extract_text(b"%PDF-1.4 this is not a real pdf", source_key="uploads/a.pdf")


# ---------------------------------------------------------------------------
# clean_text
# ---------------------------------------------------------------------------


def test_clean_unifies_newlines_and_strips_outer() -> None:
    assert clean_text("a\r\nb\rc\n\n  ") == "a\nb\nc"


def test_clean_normalizes_unicode_nfkc() -> None:
    assert clean_text("\uff28\uff45\uff4c\uff4c\uff4f\u3000\uff57") == "Hello w"


def test_clean_drops_control_characters_keeps_tab_and_newline() -> None:
    assert clean_text("a\x00\x1b\x7fb\tc\n") == "ab\tc"


def test_clean_strips_trailing_space_per_line() -> None:
    assert clean_text("mid  \nend  ") == "mid\nend"


def test_clean_collapses_blank_line_runs_keeps_single_blank() -> None:
    assert clean_text("para one\n\n\n\npara two") == "para one\n\npara two"


def test_clean_preserves_markdown_newlines_and_indentation() -> None:
    text = "# Title\n\n    code stays indented\n- item"
    assert clean_text(text) == text


# ---------------------------------------------------------------------------
# chunk_text
# ---------------------------------------------------------------------------


def test_chunk_empty_and_whitespace_only_text() -> None:
    assert chunk_text("") == []
    assert chunk_text(" \n\t  ") == []


def test_chunk_short_text_is_one_chunk() -> None:
    chunks = chunk_text("short document")
    assert chunks == [Chunk(index=0, text="short document", start=0, end=14)]


def test_chunk_windows_are_exact_slices_with_overlap() -> None:
    text = " ".join(f"word{i}" for i in range(100))
    chunks = chunk_text(text, chunk_size=50, overlap=10)

    assert len(chunks) > 1
    assert chunks[0].start == 0
    for i, chunk in enumerate(chunks):
        assert chunk.index == i
        assert chunk.text == text[chunk.start : chunk.end]
        assert chunk.text  # never empty
        assert chunk.text[0].isspace() is False  # never starts on whitespace
    for a, b in zip(chunks[:-1], chunks[1:], strict=True):
        assert b.start == a.end - 10  # overlap between consecutive chunks
    assert chunks[-1].end == len(text)


def test_chunk_breaks_at_word_boundary_when_window_lands_midword() -> None:
    text = ("a" * 30) + " " + ("b" * 30) + " " + ("c" * 30)
    chunks = chunk_text(text, chunk_size=45, overlap=5)

    assert [c.text for c in chunks] == [
        "a" * 30,
        ("a" * 5) + " " + ("b" * 30),
        ("b" * 5) + " " + ("c" * 30),
    ]
    assert chunks[-1].end == len(text)
    assert chunks[1].end - chunks[2].start == 5


def test_chunk_hard_cuts_unbreakable_long_tokens() -> None:
    text = "X" * 500  # no whitespace anywhere
    chunks = chunk_text(text, chunk_size=120, overlap=20)

    assert [c.start for c in chunks] == [0, 100, 200, 300, 400]
    assert chunks[-1].text == "X" * 100
    assert chunks[-1].end == 500


@pytest.mark.parametrize(
    "kwargs",
    [
        {"chunk_size": 0},
        {"chunk_size": -5},
        {"chunk_size": True},
        {"chunk_size": 1.5},
        {"chunk_size": "10"},
        {"chunk_size": 10, "overlap": -1},
        {"chunk_size": 10, "overlap": 10},
        {"chunk_size": 10, "overlap": 2.5},
    ],
    ids=[
        "size-0",
        "size-neg",
        "size-bool",
        "size-float",
        "size-str",
        "overlap-neg",
        "overlap-eq-size",
        "overlap-float",
    ],
)
def test_chunk_rejects_bad_parameters(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        chunk_text("some text", **kwargs)


def test_chunk_skips_leading_whitespace() -> None:
    chunks = chunk_text("   abc def ghi")
    assert chunks[0].start == 3


# ---------------------------------------------------------------------------
# document_id
# ---------------------------------------------------------------------------


def test_document_id_is_deterministic_and_version_sensitive() -> None:
    assert document_id(BUCKET, "uploads/a.pdf", "v1") == document_id(BUCKET, "uploads/a.pdf", "v1")
    assert document_id(BUCKET, "uploads/a.pdf", None) != document_id(BUCKET, "uploads/a.pdf", "v1")
    assert document_id(BUCKET, "uploads/a.pdf", None) != document_id(BUCKET, "uploads/b.pdf", None)
    assert len(document_id(BUCKET, "uploads/a.pdf", None)) == 32


# ---------------------------------------------------------------------------
# process_document (orchestration with a fake S3 client)
# ---------------------------------------------------------------------------


class FakeS3:
    """In-memory S3: serves one canned object, records writes."""

    def __init__(self, body: bytes, *, content_type: str = "text/plain") -> None:
        self.body = body
        self.content_type = content_type
        self.gets: list[tuple[str, str]] = []
        self.objects: dict[str, bytes] = {}

    def get_object(self, Bucket: str, Key: str) -> dict:
        self.gets.append((Bucket, Key))
        return {
            "Body": io.BytesIO(self.body),
            "ContentType": self.content_type,
            "ContentLength": len(self.body),
        }

    def put_object(self, Bucket: str, Key: str, Body: bytes, ContentType: str) -> None:
        assert Bucket == BUCKET
        assert ContentType == "application/json"
        self.objects[Key] = bytes(Body)


TEXT_BODY = (
    "lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor incididunt " * 25
)


def _processed_lines(body: bytes) -> list[dict]:
    return [json.loads(line) for line in body.decode("utf-8").splitlines() if line]


def test_process_document_stages_chunks_and_metadata() -> None:
    s3 = FakeS3(TEXT_BODY.encode("utf-8"))
    result = process_document(BUCKET, "uploads/report.txt", "version-1", s3_client=s3)

    assert result["document_id"] == document_id(BUCKET, "uploads/report.txt", "version-1")
    assert result["num_chunks"] > 1
    assert result["cleaned_characters"] == len(clean_text(TEXT_BODY))
    assert s3.gets == [(BUCKET, "uploads/report.txt")]

    chunks_key, metadata_key = result["keys_written"]
    assert chunks_key == f"{PROCESSED_PREFIX}{result['document_id']}/chunks.jsonl"
    assert metadata_key.endswith("/metadata.json")
    assert set(s3.objects) == {chunks_key, metadata_key}

    lines = _processed_lines(s3.objects[chunks_key])
    assert len(lines) == result["num_chunks"]
    expected = chunk_text(clean_text(TEXT_BODY))
    for line, chunk in zip(lines, expected, strict=True):
        assert line == {
            "index": chunk.index,
            "text": chunk.text,
            "start": chunk.start,
            "end": chunk.end,
        }

    metadata = json.loads(s3.objects[metadata_key])
    assert metadata["document_id"] == result["document_id"]
    assert metadata["source"] == {
        "bucket": BUCKET,
        "key": "uploads/report.txt",
        "version_id": "version-1",
        "size_bytes": len(TEXT_BODY.encode("utf-8")),
        "content_type": "text/plain",
    }
    assert metadata["pipeline"] == {"extractor": "utf-8-text", "chunker": "char-window"}
    assert metadata["chunking"] == {"chunk_size": 1200, "overlap": 200}
    assert metadata["num_chunks"] == result["num_chunks"]
    assert metadata["cleaned_characters"] == result["cleaned_characters"]
    assert metadata["ingested_at"]


def test_process_document_is_idempotent_per_version() -> None:
    s3 = FakeS3(TEXT_BODY.encode("utf-8"))
    first = process_document(BUCKET, "uploads/report.txt", "v9", s3_client=s3)
    second = process_document(BUCKET, "uploads/report.txt", "v9", s3_client=s3)

    assert second["document_id"] == first["document_id"]
    assert second["keys_written"] == first["keys_written"]
    assert set(s3.objects) == set(first["keys_written"])  # overwritten, not duplicated


def test_process_document_custom_chunk_parameters_are_recorded() -> None:
    s3 = FakeS3(TEXT_BODY.encode("utf-8"))
    result = process_document(
        BUCKET, "uploads/report.txt", None, chunk_size=300, overlap=40, s3_client=s3
    )
    chunks_key = result["keys_written"][0]
    lines = _processed_lines(s3.objects[chunks_key])
    assert all(len(line["text"]) <= 300 for line in lines)
    metadata = json.loads(s3.objects[result["keys_written"][1]])
    assert metadata["chunking"] == {"chunk_size": 300, "overlap": 40}


def test_process_document_pdf_source() -> None:
    s3 = FakeS3(make_pdf("Chapter 6 test document"), content_type="application/pdf")
    result = process_document(BUCKET, "uploads/a.pdf", "v1", s3_client=s3)
    metadata = json.loads(s3.objects[result["keys_written"][1]])
    assert metadata["pipeline"]["extractor"] == "pypdf"
    assert metadata["num_chunks"] == 1


def test_process_document_skips_blank_document() -> None:
    s3 = FakeS3(b"   \n\n  ", content_type="text/plain")
    with pytest.raises(ValueError, match="no extractable text"):
        process_document(BUCKET, "uploads/blank.txt", None, s3_client=s3)
    assert s3.objects == {}


def test_process_document_rejects_unsupported_type_before_io() -> None:
    s3 = FakeS3(b"data")
    with pytest.raises(ValueError, match="unsupported document type"):
        process_document(BUCKET, "uploads/a.docx", None, s3_client=s3)
    assert s3.objects == {}


def test_process_document_rejects_key_outside_uploads_prefix() -> None:
    s3 = FakeS3(b"data")
    with pytest.raises(ValueError, match="uploads/"):
        process_document(BUCKET, "docs/a.txt", None, s3_client=s3)
    assert s3.gets == []
