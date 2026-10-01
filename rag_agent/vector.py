import json
from dataclasses import dataclass

from psycopg import Connection

from rag_agent.embed import EMBEDDED_PREFIX, EMBEDDING_DIMENSIONS, EMBEDDINGS_SUFFIX

CHUNKS_TABLE = "rag_chunks"
HNSW_INDEX_NAME = "rag_chunks_embedding_hnsw"

_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {CHUNKS_TABLE} (
    document_id   text NOT NULL,
    chunk_index   integer NOT NULL,
    text          text NOT NULL,
    embedding     vector({EMBEDDING_DIMENSIONS}) NOT NULL,
    char_start    integer,
    char_end      integer,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS {HNSW_INDEX_NAME}
    ON {CHUNKS_TABLE} USING hnsw (embedding vector_cosine_ops);
"""


@dataclass(frozen=True)
class EmbeddingRecord:
    index: int
    text: str
    embedding: tuple[float, ...]
    start: int | None
    end: int | None


@dataclass(frozen=True)
class SearchHit:
    document_id: str
    chunk_index: int
    text: str
    distance: float


def _validate_embedding(embedding) -> tuple[float, ...]:
   
    if not isinstance(embedding, (list, tuple)):
        raise ValueError("embedding must be a list of floats")
    if len(embedding) != EMBEDDING_DIMENSIONS:
        raise ValueError(
            f"embedding has {len(embedding)} dimensions; expected {EMBEDDING_DIMENSIONS}"
        )
    try:
        return tuple(float(value) for value in embedding)
    except (TypeError, ValueError) as exc:
        raise ValueError("embedding must contain only numbers") from exc


def read_embeddings(data: bytes) -> list[EmbeddingRecord]:
    
    records: list[EmbeddingRecord] = []
    for line_number, line in enumerate(data.decode("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            embedding = _validate_embedding(record["embedding"])
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"malformed embedding record on line {line_number}: {exc}") from exc
        try:
            index = int(record["index"])
            text = str(record["text"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"malformed embedding record on line {line_number}: {exc}") from exc
        start = record.get("start")
        end = record.get("end")
        records.append(
            EmbeddingRecord(
                index=index,
                text=text,
                embedding=embedding,
                start=int(start) if start is not None else None,
                end=int(end) if end is not None else None,
            )
        )
    if not records:
        raise ValueError("embeddings file contains no records")
    return records


def ensure_schema(conn: Connection) -> None:
    
    with conn.cursor() as cursor:
        cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cursor.execute(_SCHEMA_SQL)
    conn.commit()


def replace_document(conn: Connection, *, document_id: str, records: list[EmbeddingRecord]) -> int:
    
    if not isinstance(document_id, str) or not document_id:
        raise ValueError("document_id must be a non-empty string")
    if not records:
        raise ValueError("records must not be empty")

    # Validate every record up front so a bad batch never half-inserts.
    for record in records:
        _validate_embedding(record.embedding)
        if not isinstance(record.text, str) or not record.text:
            raise ValueError("chunk text must be a non-empty string")

    with conn.cursor() as cursor:
        cursor.execute(f"DELETE FROM {CHUNKS_TABLE} WHERE document_id = %s", (document_id,))
        cursor.executemany(
            f"""
            INSERT INTO {CHUNKS_TABLE}
                (document_id, chunk_index, text, embedding, char_start, char_end)
            VALUES (%s, %s, %s, %s::vector, %s, %s)
            """,
            [
                (
                    document_id,
                    record.index,
                    record.text,
                    list(record.embedding),
                    record.start,
                    record.end,
                )
                for record in records
            ],
        )
    conn.commit()
    return len(records)


def similarity_search(
    conn: Connection,
    *,
    query_embedding,
    top_k: int,
    document_id: str | None = None,
) -> list[SearchHit]:
    query = _validate_embedding(query_embedding)
    if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 1:
        raise ValueError("top_k must be an integer >= 1")

    sql = f"""
        SELECT document_id, chunk_index, text, embedding <=> %s::vector AS distance
        FROM {CHUNKS_TABLE}
    """
    params: list = [list(query)]
    if document_id is not None:
        if not isinstance(document_id, str) or not document_id:
            raise ValueError("document_id must be a non-empty string")
        sql += " WHERE document_id = %s"
        params.append(document_id)
    sql += " ORDER BY distance ASC LIMIT %s"
    params.append(top_k)

    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        hits = [
            SearchHit(
                document_id=row[0],
                chunk_index=row[1],
                text=row[2],
                distance=float(row[3]),
            )
            for row in cursor.fetchall()
        ]
    return hits


def count_chunks(conn: Connection, *, document_id: str | None = None) -> int:
    sql = f"SELECT count(*) FROM {CHUNKS_TABLE}"
    params: tuple = ()
    if document_id is not None:
        sql += " WHERE document_id = %s"
        params = (document_id,)
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        return int(cursor.fetchone()[0])


def index_document(
    bucket: str,
    key: str,
    *,
    s3_client,
    conn: Connection,
) -> dict:
    if not isinstance(bucket, str) or not bucket:
        raise ValueError("bucket must be a non-empty string")
    if not isinstance(key, str) or not key.startswith(EMBEDDED_PREFIX):
        raise ValueError(f"key must be '{EMBEDDED_PREFIX}<document_id>/embeddings.jsonl'")
    if not key.endswith(EMBEDDINGS_SUFFIX):
        raise ValueError(f"key must be '{EMBEDDED_PREFIX}<document_id>/embeddings.jsonl'")
    document_id = key[len(EMBEDDED_PREFIX) : -len(EMBEDDINGS_SUFFIX)]
    if not document_id or "/" in document_id:
        raise ValueError(f"key must be '{EMBEDDED_PREFIX}<document_id>/embeddings.jsonl'")

    response = s3_client.get_object(Bucket=bucket, Key=key)
    records = read_embeddings(response["Body"].read())
    ensure_schema(conn)
    inserted = replace_document(conn, document_id=document_id, records=records)
    return {
        "document_id": document_id,
        "num_embeddings": inserted,
    }
