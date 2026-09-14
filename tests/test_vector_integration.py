"""Chapter 8 integration tests — need a reachable Postgres + pgvector.

These tests auto-skip when no database answers on TEST_DB_DSN (default
localhost), so the plain host suite stays green with no services running.
They run for real in the Chapter 8 verification step:

    docker compose up -d db
    uv run pytest                          # host -> localhost:5432
    docker compose run --rm app            # container -> TEST_DB_DSN=...@db:5432

Everything here exercises the same rag_agent.vector core the production
index-document Lambda runs, against a real pgvector (HNSW index, cosine
distance, filters) — the retrieval proof Chapter 9 will build on.
"""

import io
import json
import os

import pytest
from psycopg import Connection
from psycopg import connect as pg_connect

from rag_agent.embed import EMBEDDING_DIMENSIONS
from rag_agent.vector import (
    CHUNKS_TABLE,
    HNSW_INDEX_NAME,
    EmbeddingRecord,
    count_chunks,
    ensure_schema,
    index_document,
    read_embeddings,
    replace_document,
    similarity_search,
)

BUCKET = "rag-agent-dev-documents-000000000000"
DSN = os.environ.get("TEST_DB_DSN", "postgresql://rag:rag@localhost:5432/rag")


def _db_reachable() -> bool:
    """Probe once: on this machine a refused connect costs seconds, and
    evaluating per-test at collection would stall the whole run."""
    try:
        conn = pg_connect(DSN, connect_timeout=3)
        conn.close()
        return True
    except Exception:
        return False


_DB_REACHABLE = _db_reachable()

requires_db = pytest.mark.skipif(
    not _DB_REACHABLE,
    reason="pgvector DB unreachable — start it with: docker compose up -d db",
)
pytestmark = requires_db  # every test in this module needs the live DB


def basis(position: int) -> list[float]:
    """Unit vector with a single 1.0 at ``position`` (within 1024 dims)."""
    assert 0 <= position < EMBEDDING_DIMENSIONS
    return [1.0 if i == position else 0.0 for i in range(EMBEDDING_DIMENSIONS)]


def record(index: int, text: str, position: int) -> EmbeddingRecord:
    return EmbeddingRecord(
        index=index,
        text=text,
        embedding=tuple(basis(position)),
        start=index * 100,
        end=index * 100 + len(text),
    )


class FakeS3:
    """Serves one canned object (the embedded/*.jsonl file under test)."""

    def __init__(self, body: bytes) -> None:
        self.body = body

    def get_object(self, Bucket: str, Key: str) -> dict:
        assert Bucket == BUCKET
        return {"Body": io.BytesIO(self.body)}


@pytest.fixture
def conn() -> Connection:
    """Per-test connection. Session-scoped connections leak an open read
    transaction after searches, and an idle snapshot from one test module
    blocks the next module's TRUNCATE (ACCESS EXCLUSIVE) — fresh + closed
    per test keeps modules independent."""
    connection = pg_connect(DSN, connect_timeout=5)
    ensure_schema(connection)
    yield connection
    connection.close()


@pytest.fixture(autouse=True)
def _clean_table(conn: Connection) -> None:
    with conn.cursor() as cursor:
        cursor.execute(f"TRUNCATE {CHUNKS_TABLE}")
    conn.commit()


def test_schema_and_hnsw_index_exist(conn: Connection) -> None:
    with conn.cursor() as cursor:
        cursor.execute("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        assert cursor.fetchone() is not None
        cursor.execute("SELECT indexdef FROM pg_indexes WHERE indexname = %s", (HNSW_INDEX_NAME,))
        row = cursor.fetchone()
        assert row is not None
        assert "hnsw" in row[0]
        assert "vector_cosine_ops" in row[0]


def test_replace_and_search_return_nearest_first(conn: Connection) -> None:
    replace_document(
        conn,
        document_id="doc-a",
        records=[record(0, "alpha", 0), record(1, "beta", 1)],
    )
    replace_document(
        conn,
        document_id="doc-b",
        records=[record(0, "gamma", 2)],
    )
    assert count_chunks(conn) == 3

    hits = similarity_search(conn, query_embedding=basis(1), top_k=1)
    assert hits[0].document_id == "doc-a"
    assert hits[0].chunk_index == 1
    assert hits[0].text == "beta"
    assert hits[0].distance == pytest.approx(0.0)


def test_search_distances_are_cosine(conn: Connection) -> None:
    replace_document(conn, document_id="doc-a", records=[record(0, "e0", 0)])
    replace_document(conn, document_id="doc-b", records=[record(0, "e1", 1)])
    replace_document(conn, document_id="doc-c", records=[record(0, "e2", 2)])

    hits = similarity_search(conn, query_embedding=basis(0), top_k=3)

    # e0 vs query: 0 (identical); e1/e2 orthogonal: 1; ordering deterministic.
    assert [h.distance for h in hits] == pytest.approx([0.0, 1.0, 1.0])
    assert hits[0].document_id == "doc-a"


def test_document_id_filter_restricts_search(conn: Connection) -> None:
    replace_document(conn, document_id="doc-a", records=[record(0, "alpha", 0)])
    replace_document(conn, document_id="doc-b", records=[record(0, "beta", 1)])

    hits = similarity_search(conn, query_embedding=basis(0), top_k=5, document_id="doc-b")

    assert len(hits) == 1
    assert hits[0].document_id == "doc-b"
    assert hits[0].distance == pytest.approx(1.0)  # e1 orthogonal to e0


def test_top_k_caps_results_and_excess_returns_all(conn: Connection) -> None:
    replace_document(
        conn,
        document_id="doc-a",
        records=[record(0, "a0", 0), record(1, "a1", 1), record(2, "a2", 2)],
    )

    assert len(similarity_search(conn, query_embedding=basis(0), top_k=2)) == 2
    assert len(similarity_search(conn, query_embedding=basis(0), top_k=50)) == 3


def test_reindex_same_document_is_idempotent(conn: Connection) -> None:
    replace_document(conn, document_id="doc-a", records=[record(0, "v1", 0), record(1, "v1b", 1)])
    replace_document(conn, document_id="doc-a", records=[record(0, "v1", 0), record(1, "v1b", 1)])

    assert count_chunks(conn, document_id="doc-a") == 2  # replaced, not duplicated


def test_reindex_with_fewer_rows_converges(conn: Connection) -> None:
    replace_document(conn, document_id="doc-a", records=[record(0, "x", 0), record(1, "y", 1)])
    replace_document(conn, document_id="doc-a", records=[record(0, "x", 0)])

    assert count_chunks(conn, document_id="doc-a") == 1


def test_wrong_dimensions_are_rejected_and_table_unchanged(conn: Connection) -> None:
    replace_document(conn, document_id="doc-a", records=[record(0, "ok", 0)])
    bad = EmbeddingRecord(index=1, text="bad", embedding=(1.0, 2.0, 3.0), start=None, end=None)

    with pytest.raises(ValueError, match="expected 1024"):
        replace_document(conn, document_id="doc-a", records=[bad])

    assert count_chunks(conn, document_id="doc-a") == 1  # validation ran before any write


def test_index_document_end_to_end(conn: Connection) -> None:
    body = "".join(
        json.dumps(
            {
                "index": 0,
                "text": "chunk zero",
                "embedding": basis(5),
                "input_text_token_count": 4,
                "start": 0,
                "end": 10,
            }
        )
        + "\n"
        for _ in range(1)
    ).encode("utf-8")
    s3 = FakeS3(body)

    result = index_document(BUCKET, "embedded/doc-e2e/embeddings.jsonl", s3_client=s3, conn=conn)

    assert result == {"document_id": "doc-e2e", "num_embeddings": 1}
    hits = similarity_search(conn, query_embedding=basis(5), top_k=1)
    assert hits[0].document_id == "doc-e2e"
    assert hits[0].text == "chunk zero"
    assert hits[0].distance == pytest.approx(0.0)

    # Re-running the same file converges (S3 retry semantics).
    index_document(BUCKET, "embedded/doc-e2e/embeddings.jsonl", s3_client=s3, conn=conn)
    assert count_chunks(conn, document_id="doc-e2e") == 1


def test_metadata_offsets_round_trip(conn: Connection) -> None:
    replace_document(conn, document_id="doc-a", records=[record(7, "seventh chunk", 3)])

    hits = similarity_search(conn, query_embedding=basis(3), top_k=1)
    hit = hits[0]
    assert hit.chunk_index == 7
    assert hit.text == "seventh chunk"


def test_parse_then_insert_matches_read_embeddings(conn: Connection) -> None:
    """The full chain the handler runs: file bytes -> parse -> insert."""
    line = json.dumps(
        {"index": 0, "text": "parsed", "embedding": basis(9), "start": 0, "end": 6}
    ).encode("utf-8")
    records = read_embeddings(line)
    replace_document(conn, document_id="doc-parse", records=records)

    hits = similarity_search(conn, query_embedding=basis(9), top_k=1)
    assert hits[0].document_id == "doc-parse"
    assert hits[0].text == "parsed"
