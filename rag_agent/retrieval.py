"""Chapter 9 retrieval core: question -> query embedding -> vector search.

The independent retrieval loop Chapter 8's store was built to serve:

    user question
        -> embed with the same Titan model as ingest (rag_agent.embed)
        -> cosine search over rag_chunks (rag_agent.vector)
        -> top-K ranked chunks with their text (ready-to-prompt context)

No LLM here — that is Chapter 10. Keeping retrieval standalone is the point:
it can be exercised and tuned (top-K, filters, distance cutoffs) against the
vector store before any generation exists, which makes the Chapter 10
answer path a thin prompt over a proven retriever.

Because ingest embeddings are normalized unit vectors, the question
embedding lives in the same space and plain cosine distance ranks chunks.
Filters pass through as exact WHERE clauses (e.g. scoping to one
document_id). The connection and the Bedrock client are injected, so the
core is exercised in gated integration tests against compose pgvector with
a fake Bedrock client, and in production by the API-Gateway-backed
retrieve-document Lambda.
"""

from rag_agent.embed import embed_texts
from rag_agent.vector import similarity_search

DEFAULT_TOP_K = 5
MAX_TOP_K = 20
MAX_QUESTION_LENGTH = 2000


def retrieve(
    question: str,
    *,
    conn,
    bedrock_client,
    top_k: int = DEFAULT_TOP_K,
    document_id: str | None = None,
) -> dict:
    """Return the top-K chunks nearest to ``question``.

    Validates the question/top_k (permanent, client-correctable problems ->
    ValueError), embeds the question with bounded retry, then searches.
    Transient Bedrock/DB failures propagate for the caller to handle.

    Returns a response-contract dict:
    ``{"question": ..., "results": [{"document_id", "chunk_index",
    "text", "distance"}...]}`` ordered nearest-first. Chunk text rides in
    the result so the (Chapter 10) prompt builder needs no extra read.
    """
    if not isinstance(question, str):
        raise ValueError("question must be a string")
    question = question.strip()
    if not question:
        raise ValueError("question must not be empty")
    if len(question) > MAX_QUESTION_LENGTH:
        raise ValueError(f"question exceeds {MAX_QUESTION_LENGTH} characters")
    if not isinstance(top_k, int) or isinstance(top_k, bool) or not 1 <= top_k <= MAX_TOP_K:
        raise ValueError(f"top_k must be an integer in [1, {MAX_TOP_K}]")
    if document_id is not None and (not isinstance(document_id, str) or not document_id):
        raise ValueError("document_id must be a non-empty string")

    [embedding_result] = embed_texts([question], bedrock_client=bedrock_client)
    hits = similarity_search(
        conn,
        query_embedding=embedding_result["embedding"],
        top_k=top_k,
        document_id=document_id,
    )

    return {
        "question": question,
        "results": [
            {
                "document_id": hit.document_id,
                "chunk_index": hit.chunk_index,
                "text": hit.text,
                "distance": hit.distance,
            }
            for hit in hits
        ],
    }
