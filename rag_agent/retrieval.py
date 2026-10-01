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
