"""Chapter 9 integration tests: retrieval loop over live pgvector.

Auto-skip when no database answers (see test_vector_integration.py for the
pattern). Bedrock is faked — question text maps to a deterministic unit
vector — so the whole retrieval loop (embed call shape -> cosine search ->
ranked context) is proven against the real store without any model cost.

Run: docker compose up -d db, then `uv run pytest` (localhost) or
`docker compose run --rm app` (in-container DSN).
"""

import io
import json
import os

import pytest
from psycopg import Connection
from psycopg import connect as pg_connect

from rag_agent.embed import EMBEDDING_DIMENSIONS
from rag_agent.retrieval import retrieve
from rag_agent.vector import CHUNKS_TABLE, EmbeddingRecord, ensure_schema, replace_document

DSN = os.environ.get("TEST_DB_DSN", "postgresql://rag:rag@localhost:5432/rag")


def _db_reachable() -> bool:
    try:
        conn = pg_connect(DSN, connect_timeout=3)
        conn.close()
        return True
    except Exception:
        return False


_DB_REACHABLE = _db_reachable()

pytestmark = pytest.mark.skipif(
    not _DB_REACHABLE,
    reason="pgvector DB unreachable — start it with: docker compose up -d db",
)


def basis(position: int) -> list[float]:
    assert 0 <= position < EMBEDDING_DIMENSIONS
    return [1.0 if i == position else 0.0 for i in range(EMBEDDING_DIMENSIONS)]


class FakeBedrock:
    """Maps question substrings to deterministic unit vectors, the way the
    real model maps semantically-close text to nearby vectors."""

    def invoke_model(self, **kwargs) -> dict:
        text = json.loads(kwargs["body"])["inputText"]
        if "beta" in text:
            vector = basis(1)
        elif "gamma" in text:
            vector = basis(2)
        else:
            vector = basis(0)  # "alpha" questions and anything else
        payload = {"embedding": vector, "inputTextTokenCount": 5}
        return {"body": io.BytesIO(json.dumps(payload).encode("utf-8"))}


def chunk(index: int, text: str, position: int) -> EmbeddingRecord:
    return EmbeddingRecord(
        index=index,
        text=text,
        embedding=tuple(basis(position)),
        start=index * 100,
        end=index * 100 + len(text),
    )


@pytest.fixture
def conn() -> Connection:
    """Per-test connection: searches leave an open read transaction, and an
    idle snapshot blocks the next module's TRUNCATE (see the identical
    fixture in test_vector_integration.py)."""
    connection = pg_connect(DSN, connect_timeout=5)
    ensure_schema(connection)
    yield connection
    connection.close()


@pytest.fixture(autouse=True)
def _clean_table(conn: Connection) -> None:
    with conn.cursor() as cursor:
        cursor.execute(f"TRUNCATE {CHUNKS_TABLE}")
    conn.commit()


def test_retrieve_returns_nearest_chunk_first_with_context(conn: Connection) -> None:
    replace_document(conn, document_id="doc-a", records=[chunk(0, "alpha chunk", 0)])
    replace_document(conn, document_id="doc-b", records=[chunk(0, "beta chunk", 1)])
    bedrock = FakeBedrock()

    result = retrieve("alpha question", conn=conn, bedrock_client=bedrock)

    assert result["question"] == "alpha question"
    results = result["results"]
    assert len(results) == 2  # default top_k 5 >= rows
    assert results[0] == {
        "document_id": "doc-a",
        "chunk_index": 0,
        "text": "alpha chunk",
        "distance": 0.0,
    }
    assert results[1]["document_id"] == "doc-b"
    assert results[1]["distance"] == 1.0  # beta orthogonal to alpha


def test_retrieve_top_k_caps_ranked_results(conn: Connection) -> None:
    replace_document(
        conn,
        document_id="doc-a",
        records=[chunk(0, "a0", 0), chunk(1, "a1", 1), chunk(2, "a2", 2)],
    )

    result = retrieve("alpha question", conn=conn, bedrock_client=FakeBedrock(), top_k=2)

    # a0 is distance 0; a1/a2 both sit at distance 1.0, so HNSW may return
    # either second — assert the distances, not the tie order.
    assert len(result["results"]) == 2
    assert result["results"][0]["chunk_index"] == 0
    assert result["results"][0]["distance"] == 0.0
    assert sorted(r["distance"] for r in result["results"]) == [0.0, 1.0]


def test_retrieve_document_id_filter_scopes_search(conn: Connection) -> None:
    replace_document(conn, document_id="doc-a", records=[chunk(0, "alpha chunk", 0)])
    replace_document(conn, document_id="doc-b", records=[chunk(0, "beta chunk", 1)])

    result = retrieve(
        "alpha question",
        conn=conn,
        bedrock_client=FakeBedrock(),
        document_id="doc-b",
    )

    assert [r["document_id"] for r in result["results"]] == ["doc-b"]
    assert result["results"][0]["distance"] == 1.0  # only doc-b exists in scope


def test_retrieve_beta_question_ranks_beta_document_first(conn: Connection) -> None:
    """The fake model separates topics: a beta question must not match
    the alpha document — semantic routing, not keyword routing."""
    replace_document(conn, document_id="doc-a", records=[chunk(0, "alpha chunk", 0)])
    replace_document(conn, document_id="doc-b", records=[chunk(0, "beta chunk", 1)])

    result = retrieve("beta question", conn=conn, bedrock_client=FakeBedrock())

    assert result["results"][0]["document_id"] == "doc-b"
    assert result["results"][0]["distance"] == 0.0
