"""Chapter 10 integration tests: full answer path over live pgvector.

Auto-skip without a reachable DB (pattern shared with the other gated
suites). Retrieval runs against real pgvector; the LLM is faked (a canned
Claude-style response), so the whole loop — real retrieval -> explicit
prompt build -> Messages-API call shape -> answer + cited sources — is
proven without model cost. Real Titan/Claude calls happen at apply time.
"""

import io
import json
import os

import pytest
from psycopg import Connection
from psycopg import connect as pg_connect

from rag_agent.embed import EMBEDDING_DIMENSIONS
from rag_agent.generate import GENERATION_MODEL_ID, generate_answer
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
    """One fake serving both calls: query embedding (Titan shape) and the
    generation call (Claude shape), selected by the modelId it receives."""

    def __init__(self) -> None:
        self.generation_calls: list[dict] = []

    def invoke_model(self, **kwargs) -> dict:
        model_id = kwargs["modelId"]
        if model_id == GENERATION_MODEL_ID:
            prompt = json.loads(kwargs["body"])["messages"][0]["content"]
            self.generation_calls.append(
                {"model_id": model_id, "prompt": prompt, "body": kwargs["body"]}
            )
            payload = {"content": [{"type": "text", "text": "The answer, from context."}]}
            return {"body": io.BytesIO(json.dumps(payload).encode("utf-8"))}
        # Query embedding: beta questions -> basis(1), else basis(0).
        text = json.loads(kwargs["body"])["inputText"]
        vector = basis(1) if "beta" in text else basis(0)
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
    """Per-test connection (see test_vector_integration.py rationale)."""
    connection = pg_connect(DSN, connect_timeout=5)
    ensure_schema(connection)
    yield connection
    connection.close()


@pytest.fixture(autouse=True)
def _clean_table(conn: Connection) -> None:
    with conn.cursor() as cursor:
        cursor.execute(f"TRUNCATE {CHUNKS_TABLE}")
    conn.commit()


def test_generate_answer_answers_from_retrieved_context(conn: Connection) -> None:
    replace_document(conn, document_id="doc-a", records=[chunk(0, "alpha chunk text", 0)])
    replace_document(conn, document_id="doc-b", records=[chunk(0, "beta chunk text", 1)])
    bedrock = FakeBedrock()

    result = generate_answer("alpha question", conn=conn, bedrock_client=bedrock)

    assert result["question"] == "alpha question"
    assert result["answer"] == "The answer, from context."
    assert result["model_id"] == GENERATION_MODEL_ID
    # The answer was built from the alpha chunk, ranked first with citation.
    assert result["sources"][0]["document_id"] == "doc-a"
    assert result["sources"][0]["distance"] == 0.0
    assert len(bedrock.generation_calls) == 1
    prompt = bedrock.generation_calls[0]["prompt"]
    assert "[1] (doc-a chunk 0) alpha chunk text" in prompt
    assert "Question: alpha question" in prompt


def test_generate_answer_skips_llm_with_no_documents(conn: Connection) -> None:
    bedrock = FakeBedrock()

    result = generate_answer("anything", conn=conn, bedrock_client=bedrock)

    assert result["answer"] == "No relevant documents were found for this question."
    assert result["sources"] == []
    assert bedrock.generation_calls == []  # no tokens spent


def test_generate_answer_document_scope_uses_only_that_document(conn: Connection) -> None:
    replace_document(conn, document_id="doc-a", records=[chunk(0, "alpha chunk text", 0)])
    replace_document(conn, document_id="doc-b", records=[chunk(0, "beta chunk text", 1)])
    bedrock = FakeBedrock()

    result = generate_answer(
        "alpha question", conn=conn, bedrock_client=bedrock, document_id="doc-b"
    )

    # Scoped search returns only beta's chunk (distance 1 to the query),
    # and that chunk is the sole source cited in the prompt.
    assert [s["document_id"] for s in result["sources"]] == ["doc-b"]
    prompt = bedrock.generation_calls[0]["prompt"]
    assert "[1] (doc-b chunk 0) beta chunk text" in prompt
    assert "alpha chunk" not in prompt
