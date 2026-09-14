"""Chapter 11 — the complete pipeline, end to end.

Every earlier suite proves one stage. This one proves they are *connected*:
the real Lambda handlers run in sequence, each triggered by the event the
previous stage's S3 bucket notification actually delivers, over an in-memory
S3 (the object bytes), a fake Bedrock (no model cost) and the live pgvector
store — the same code path an applied deployment takes:

    uploads/handbook.txt
      -> ingest-document  (S3 ObjectCreated) -> processed/<id>/{chunks.jsonl,metadata.json}
      -> embed-document   (S3 ObjectCreated) -> embedded/<id>/embeddings.jsonl
      -> index-document   (S3 ObjectCreated) -> rag_chunks rows (pgvector)
      -> retrieve-document (API GW v2: POST /documents/search) -> ranked chunks
      -> retrieve-document (API GW v2: POST /documents/answer) -> cited answer

The chain is held together by two contracts this suite asserts directly:
- **key routing** — each stage writes exactly the keys (prefix + suffix) the
  next stage's notification filter matches (infra/dev/ingest_lambda.tf), and
  never a key that would retrigger its own stage;
- **document_id** — sha256(bucket, key, version) derived at ingest from the
  key path at every later stage, so provenance survives the whole pipeline
  and re-running a stage converges instead of duplicating rows.

Auto-skips when no database answers on TEST_DB_DSN (same convention as the
other integration suites): `docker compose up -d db`.
"""

import io
import json
import os
from dataclasses import dataclass

import embed_handler
import index_handler
import ingest_handler
import pytest
import retrieve_handler
from psycopg import Connection
from psycopg import connect as pg_connect

import rag_agent.embed as embed_mod
import rag_agent.ingest as ingest_mod
from rag_agent.embed import (
    CHUNKS_SUFFIX,
    EMBEDDED_PREFIX,
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL_ID,
    EMBEDDINGS_SUFFIX,
)
from rag_agent.generate import GENERATION_MODEL_ID
from rag_agent.ingest import PROCESSED_PREFIX, document_id
from rag_agent.storage import UPLOADS_PREFIX
from rag_agent.vector import CHUNKS_TABLE, count_chunks, ensure_schema, read_embeddings

BUCKET = "rag-agent-dev-documents-000000000000"
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


# --- fakes -------------------------------------------------------------------


def basis(position: int) -> list[float]:
    """Unit vector with a single 1.0 at ``position`` (within 1024 dims)."""
    assert 0 <= position < EMBEDDING_DIMENSIONS
    return [1.0 if i == position else 0.0 for i in range(EMBEDDING_DIMENSIONS)]


# Topic word -> the dimension its vector points at. Checked in this order, so
# a chunk containing both topics ranks as the first one (documented, not a
# hidden tie-break): the fixture keeps one pure chunk per topic regardless.
TOPIC_POSITIONS = {"alpha": 0, "beta": 1}
_OFF_TOPIC = 2


def vector_for(text: str) -> list[float]:
    """Deterministic pseudo-embedding: topic word -> its own dimension.

    Stands in for the real model's semantic geometry: text about the same
    topic lands on the same axis, so a topic question is cosine-distance 0.0
    from that topic's chunks and 1.0 from the other topic's.
    """
    lowered = text.lower()
    for topic, position in TOPIC_POSITIONS.items():
        if topic in lowered:
            return basis(position)
    return basis(_OFF_TOPIC)


class FakeS3:
    """In-memory S3: the whole pipeline reads and writes one object dict."""

    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = dict(objects or {})
        self.writes: list[str] = []

    def get_object(self, Bucket: str, Key: str) -> dict:
        assert Bucket == BUCKET
        return {"Body": io.BytesIO(self.objects[Key]), "ContentType": "text/plain"}

    def put_object(self, Bucket: str, Key: str, Body: bytes, ContentType: str = "") -> None:
        assert Bucket == BUCKET
        self.objects[Key] = bytes(Body)
        self.writes.append(Key)


class FakeBedrock:
    """One fake for both Bedrock calls, selected by the modelId it receives.

    Embedding (Titan: shape ``embedding``/``inputTextTokenCount``) and
    generation (Claude: Messages-API ``content`` blocks) share the invoke
    API. Generation prompts are recorded so context injection is asserted
    rather than assumed.
    """

    ANSWER = "According to [1], the beta policy applies."

    def __init__(self) -> None:
        self.embedding_calls: list[str] = []
        self.generation_calls: list[dict] = []

    def invoke_model(self, **kwargs) -> dict:
        model_id = kwargs["modelId"]
        body = json.loads(kwargs["body"])
        if model_id == GENERATION_MODEL_ID:
            self.generation_calls.append(
                {"model_id": model_id, "prompt": body["messages"][0]["content"]}
            )
            payload = {"content": [{"type": "text", "text": self.ANSWER}]}
        else:
            assert model_id == EMBEDDING_MODEL_ID
            text = body["inputText"]
            self.embedding_calls.append(text)
            payload = {"embedding": vector_for(text), "inputTextTokenCount": 5}
        return {"body": io.BytesIO(json.dumps(payload).encode("utf-8"))}


# --- fixture document --------------------------------------------------------


ALPHA_SENTENCE = "Alpha policy: the alpha desk triages an alpha request end to end. "
BETA_SENTENCE = "Beta policy: the beta desk triages a beta request end to end. "


def handbook_text() -> str:
    """Two well-separated topics; each section is wider than one chunk.

    30 repeats is ~1800 characters per section against the 1200/200 chunk
    contract, so each topic owns at least one chunk that carries only it —
    which is what makes the retrieval assertion below meaningful.
    """
    return f"{ALPHA_SENTENCE * 30}\n\n{BETA_SENTENCE * 30}\n"


# --- event shapes ------------------------------------------------------------


def s3_event(key: str, version_id: str = "v1") -> dict:
    """Direct S3 ObjectCreated event — what ingest/embed/index are invoked with."""
    return {
        "Records": [
            {
                "eventName": "ObjectCreated:Put",
                "s3": {
                    "bucket": {"name": BUCKET},
                    "object": {"key": key, "versionId": version_id},
                },
            }
        ]
    }


def api_event(route: str, payload: dict) -> dict:
    """API Gateway v2 proxy event — what the retrieve routes are invoked with."""
    return {
        "routeKey": route,
        "rawPath": route.split(" ", 1)[1],
        "body": json.dumps(payload),
        "isBase64Encoded": False,
    }


# --- driving the chain -------------------------------------------------------


@dataclass
class Pipeline:
    """The pipeline as the platform drives it: one method per stage."""

    s3: FakeS3
    bedrock: FakeBedrock
    conn: Connection

    def upload(self, key: str, data: bytes, version_id: str = "v1") -> dict:
        """Land raw bytes in the bucket, then run ingest-document on the event."""
        assert key.startswith(UPLOADS_PREFIX)
        self.s3.objects[key] = data
        response = ingest_handler.lambda_handler(s3_event(key, version_id), None)
        assert response["failed"] == [], response["failed"]
        assert response["skipped"] == [], response["skipped"]
        return response["processed"][0]

    def embed(self, doc_id: str) -> dict:
        """Run embed-document on the chunks write ingest just made."""
        key = f"{PROCESSED_PREFIX}{doc_id}{CHUNKS_SUFFIX}"
        response = embed_handler.lambda_handler(s3_event(key), None)
        assert response["failed"] == [], response["failed"]
        return response["processed"][0]

    def index(self, doc_id: str) -> dict:
        """Run index-document on the embeddings write embed just made."""
        key = f"{EMBEDDED_PREFIX}{doc_id}{EMBEDDINGS_SUFFIX}"
        response = index_handler.lambda_handler(s3_event(key), None)
        assert response["failed"] == [], response["failed"]
        return response["processed"][0]

    def run(self, key: str, data: bytes, version_id: str = "v1") -> str:
        """Full ingestion side of the pipeline; returns the document_id."""
        upload = self.upload(key, data, version_id)
        doc_id = upload["document_id"]
        self.embed(doc_id)
        self.index(doc_id)
        return doc_id

    def query(self, route: str, question: str, **extra) -> dict:
        """Hit a retrieve-document route exactly as API Gateway does."""
        payload = {"question": question, **extra}
        response = retrieve_handler.lambda_handler(api_event(route, payload), None)
        assert response["statusCode"] == 200, response["body"]
        return json.loads(response["body"])


SEARCH_ROUTE = "POST /documents/search"
ANSWER_ROUTE = "POST /documents/answer"


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


@pytest.fixture
def pipeline(monkeypatch, conn: Connection) -> Pipeline:
    """The real handlers, wired to in-memory S3, a fake Bedrock, and the DB.

    Only the client/connection builders are stubbed: every stage's event
    parsing, outcome classification, and core logic is the production code.
    """
    s3 = FakeS3()
    bedrock = FakeBedrock()
    monkeypatch.setattr(ingest_mod, "_get_client", lambda: s3)
    monkeypatch.setattr(embed_mod, "_get_s3_client", lambda: s3)
    monkeypatch.setattr(embed_mod, "_get_client", lambda: bedrock)
    monkeypatch.setattr(index_handler, "_get_s3", lambda: s3)
    monkeypatch.setattr(index_handler, "_get_conn", lambda: conn)
    monkeypatch.setattr(retrieve_handler, "_get_conn", lambda: conn)
    monkeypatch.setattr(retrieve_handler, "_get_bedrock", lambda: bedrock)
    return Pipeline(s3=s3, bedrock=bedrock, conn=conn)


# --- the pipeline ------------------------------------------------------------


def test_upload_to_cited_answer(pipeline: Pipeline) -> None:
    """Document bytes in, cited answer out — every stage in one flow."""
    upload = pipeline.upload("uploads/handbook.txt", handbook_text().encode("utf-8"))
    doc_id = upload["document_id"]

    # Ingest derived the same id every later stage will re-derive from the path.
    assert doc_id == document_id(BUCKET, "uploads/handbook.txt", "v1")
    assert upload["num_chunks"] >= 3

    # Stage 1 wrote exactly the keys the embed notification listens on
    # (processed/ + .jsonl) and nothing that could retrigger ingestion.
    assert pipeline.s3.writes == [
        f"{PROCESSED_PREFIX}{doc_id}{CHUNKS_SUFFIX}",
        f"{PROCESSED_PREFIX}{doc_id}/metadata.json",
    ]
    metadata = json.loads(pipeline.s3.objects[f"{PROCESSED_PREFIX}{doc_id}/metadata.json"])
    assert metadata["num_chunks"] == upload["num_chunks"]
    assert metadata["source"]["key"] == "uploads/handbook.txt"

    # Stage 2: one vector per chunk, text riding along for retrieval.
    embedded = pipeline.embed(doc_id)
    embedded_key = f"{EMBEDDED_PREFIX}{doc_id}{EMBEDDINGS_SUFFIX}"
    assert embedded["num_embeddings"] == upload["num_chunks"]
    assert embedded_key == f"{EMBEDDED_PREFIX}{doc_id}/embeddings.jsonl"
    records = read_embeddings(pipeline.s3.objects[embedded_key])
    assert len(records) == upload["num_chunks"]
    assert [record.index for record in records] == list(range(upload["num_chunks"]))
    assert all(len(record.embedding) == EMBEDDING_DIMENSIONS for record in records)

    # Stage 3: rows in pgvector, same id, same count.
    indexed = pipeline.index(doc_id)
    assert indexed["num_embeddings"] == upload["num_chunks"]
    assert count_chunks(pipeline.conn, document_id=doc_id) == upload["num_chunks"]

    # Nothing the pipeline wrote can retrigger an earlier stage.
    assert all(key.startswith((PROCESSED_PREFIX, EMBEDDED_PREFIX)) for key in pipeline.s3.writes)

    # Retrieval: a beta question ranks a beta chunk first, with its text.
    search = pipeline.query(SEARCH_ROUTE, "What does the beta policy cover?")
    assert search["question"] == "What does the beta policy cover?"
    top = search["results"][0]
    assert top["document_id"] == doc_id
    assert "Beta policy" in top["text"]
    assert top["distance"] == pytest.approx(0.0, abs=1e-6)
    # ... and the alpha chunk is present but further away (one topic apart).
    assert any("Alpha policy" in hit["text"] for hit in search["results"])

    # Generation: the answer comes from the retrieved context, cited.
    answer = pipeline.query(ANSWER_ROUTE, "What does the beta policy cover?")
    assert answer["answer"] == FakeBedrock.ANSWER
    assert answer["model_id"] == GENERATION_MODEL_ID
    assert answer["sources"][0]["text"] == top["text"]
    prompt = pipeline.bedrock.generation_calls[0]["prompt"]
    assert f"[1] ({doc_id} chunk {top['chunk_index']}) {top['text']}" in prompt
    assert "Question: What does the beta policy cover?" in prompt


def test_replaying_the_pipeline_converges(pipeline: Pipeline) -> None:
    """An S3 retry of any stage re-runs the same work — never duplicates it."""
    data = handbook_text().encode("utf-8")
    doc_id = pipeline.run("uploads/handbook.txt", data)
    first_chunks = pipeline.upload("uploads/handbook.txt", data)["num_chunks"]
    assert count_chunks(pipeline.conn, document_id=doc_id) == first_chunks

    # Replay every stage on the same object version.
    replay = pipeline.upload("uploads/handbook.txt", data)
    assert replay["document_id"] == doc_id
    assert pipeline.embed(doc_id)["num_embeddings"] == first_chunks
    assert pipeline.index(doc_id)["num_embeddings"] == first_chunks

    assert count_chunks(pipeline.conn, document_id=doc_id) == first_chunks
    search = pipeline.query(SEARCH_ROUTE, "What does the alpha policy cover?")
    assert search["results"][0]["document_id"] == doc_id


def test_document_id_scopes_retrieval_across_the_chain(pipeline: Pipeline) -> None:
    """Two uploads -> two document_ids -> per-document retrieval, end to end."""
    handbook_id = pipeline.run("uploads/handbook.txt", handbook_text().encode("utf-8"))
    alpha_only_id = pipeline.run("uploads/alpha-only.txt", (ALPHA_SENTENCE * 40).encode("utf-8"))
    # Each upload owns a distinct id, derived at ingest and re-derived from
    # the key path at every later stage.
    assert handbook_id != alpha_only_id
    assert alpha_only_id == document_id(BUCKET, "uploads/alpha-only.txt", "v1")

    unscoped = pipeline.query(SEARCH_ROUTE, "What does the alpha policy cover?", top_k=20)
    assert {hit["document_id"] for hit in unscoped["results"]} == {
        handbook_id,
        alpha_only_id,
    }

    scoped = pipeline.query(
        SEARCH_ROUTE,
        "What does the alpha policy cover?",
        top_k=20,
        document_id=alpha_only_id,
    )
    assert scoped["results"], scoped
    assert {hit["document_id"] for hit in scoped["results"]} == {alpha_only_id}
    assert scoped["results"][0]["distance"] == pytest.approx(0.0, abs=1e-6)

    # The answer route honours the same filter (one document's context only).
    answer = pipeline.query(
        ANSWER_ROUTE, "What does the alpha policy cover?", document_id=alpha_only_id
    )
    assert {source["document_id"] for source in answer["sources"]} == {alpha_only_id}
