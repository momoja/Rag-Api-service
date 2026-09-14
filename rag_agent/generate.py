"""Chapter 10 generation core: retrieved context -> explicit prompt -> LLM.

The final RAG stage. Chapter 9's retriever stays untouched beneath this
module: the answer path is retrieval plus a deliberately explicit prompt,
so retrieval quality is never entangled with generation.

Flow:

    question
        -> retrieve (rag_agent.retrieval: embed query, cosine top-K)
        -> build_prompt: numbered context, verbatim chunk text, the
           question, and a strict "answer only from context, cite [n]"
           instruction — context injection is explicit, nothing hidden
        -> Bedrock LLM (Claude via the Messages API)
        -> {"answer", "sources", "question"}

If retrieval returns nothing, no LLM call is made (no point spending
tokens) and the answer states that no relevant documents were found.

Model config (decision, ch10): Claude Haiku by default — fast and cheap
for dev-scale answers over 1200-char chunks; swap by changing the one
constant (and the IAM model ARN in infra if the model family changes).
Model availability is checked at apply time, not here.
"""

import json
from functools import partial

from botocore.exceptions import ClientError

from rag_agent.bedrock import retry_call
from rag_agent.retrieval import DEFAULT_TOP_K, retrieve

GENERATION_MODEL_ID = "anthropic.claude-3-5-haiku-20241022-v1:0"
GENERATION_MAX_TOKENS = 512

_SYSTEM_PROMPT = (
    "You answer questions about a document collection. Answer using ONLY the "
    "numbered context passages below. If the context does not contain the "
    "answer, say so plainly — do not invent facts. Cite passages as [n] where "
    "n is the passage number you used."
)


def build_prompt(question: str, results: list[dict]) -> str:
    """Build the user prompt: numbered context, then the question.

    ``results`` is the retrieval contract list (document_id, chunk_index,
    text, distance). Pure and deterministic — pinned by tests.
    """
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must not be empty")
    if not results:
        raise ValueError("results must not be empty")

    passages = "\n".join(
        f"[{i + 1}] ({result['document_id']} chunk {result['chunk_index']}) {result['text']}"
        for i, result in enumerate(results)
    )
    return (
        "Context passages:\n"
        f"{passages}\n\n"
        f"Question: {question.strip()}\n\n"
        "Answer the question using only the context passages above."
    )


def generate_answer(
    question: str,
    *,
    conn,
    bedrock_client,
    top_k: int = DEFAULT_TOP_K,
    document_id: str | None = None,
    max_tokens: int = GENERATION_MAX_TOKENS,
    model_id: str = GENERATION_MODEL_ID,
) -> dict:
    """Answer ``question`` from retrieved context via the Bedrock LLM.

    Validation of question/top_k/document_id is delegated to
    rag_agent.retrieval.retrieve (ValueError on client-correctable input).
    No LLM call when retrieval finds nothing — returns a plain
    "no relevant documents" answer with empty sources.

    Returns ``{"question", "answer", "sources", "model_id"}`` where
    ``sources`` is the retrieval result list (ranked, with text), enabling
    citation and downstream checks of what the answer was built from.
    """
    result = retrieve(
        question,
        conn=conn,
        bedrock_client=bedrock_client,
        top_k=top_k,
        document_id=document_id,
    )
    results = result["results"]
    if not results:
        return {
            "question": result["question"],
            "answer": "No relevant documents were found for this question.",
            "sources": [],
            "model_id": model_id,
        }

    prompt = build_prompt(result["question"], results)
    answer = _call_llm(
        prompt,
        bedrock_client=bedrock_client,
        max_tokens=max_tokens,
        model_id=model_id,
    )
    return {
        "question": result["question"],
        "answer": answer,
        "sources": results,
        "model_id": model_id,
    }


def _call_llm(
    prompt: str,
    *,
    bedrock_client,
    max_tokens: int,
    model_id: str,
) -> str:
    """One Claude Messages-API call with the shared bounded retry."""
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens < 1:
        raise ValueError("max_tokens must be an integer >= 1")

    body = json.dumps(
        {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "system": _SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode("utf-8")

    def invoke() -> dict:
        response = bedrock_client.invoke_model(
            body=body,
            modelId=model_id,
            accept="application/json",
            contentType="application/json",
        )
        payload = json.loads(response["body"].read())
        try:
            content = payload["content"]
            if not isinstance(content, list) or not content:
                raise ValueError("model response has no content blocks")
            text = content[0].get("text")
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError(f"unexpected model response: {exc}") from exc
        if not isinstance(text, str) or not text:
            raise ValueError("model response content is not text")
        return text.strip()

    try:
        return retry_call(partial(invoke), label="bedrock generation")
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "ValidationException":
            # Permanent request problem (e.g. prompt too long for the model):
            # surface as ValueError so callers skip, never retry forever.
            raise ValueError(f"model rejected the request: {exc}") from exc
        raise
