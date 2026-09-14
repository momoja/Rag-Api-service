"""Offline tests for rag_agent.generate — prompt building and LLM plumbing.

The retrieval leg is monkeypatched (its live-DB behavior is proven in
test_retrieval_integration.py / test_generate_integration.py); Bedrock is a
fake. These tests pin the prompt contract and the Claude Messages-API call
shape — the pieces that determine answer quality and cost.
"""

import io
import json

import pytest
from botocore.exceptions import ClientError

import rag_agent.bedrock as bedrock_mod
import rag_agent.generate as generate
from rag_agent.generate import GENERATION_MODEL_ID, build_prompt, generate_answer

RESULT = {
    "document_id": "doc-a",
    "chunk_index": 0,
    "text": "The rent policy allows two pets.",
    "distance": 0.0,
}
RESULT2 = {
    "document_id": "doc-b",
    "chunk_index": 3,
    "text": "Tenants must give 30 days notice.",
    "distance": 0.1,
}


class FakeLLM:
    """Serves a canned Claude-style response; records the request."""

    def __init__(self, text: str = "Two pets are allowed.") -> None:
        self.text = text
        self.calls: list[dict] = []

    def invoke_model(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        payload = {"content": [{"type": "text", "text": self.text}]}
        return {"body": io.BytesIO(json.dumps(payload).encode("utf-8"))}


# ---------------------------------------------------------------------------
# build_prompt
# ---------------------------------------------------------------------------


def test_build_prompt_numbers_and_injects_context() -> None:
    prompt = build_prompt("How many pets are allowed?", [RESULT, RESULT2])

    assert prompt.startswith("Context passages:\n")
    assert "[1] (doc-a chunk 0) The rent policy allows two pets." in prompt
    assert "[2] (doc-b chunk 3) Tenants must give 30 days notice." in prompt
    assert prompt.index("[1]") < prompt.index("[2]")  # retrieval order kept
    assert "\n\nQuestion: How many pets are allowed?" in prompt
    assert prompt.endswith("Answer the question using only the context passages above.")


def test_build_prompt_rejects_empty_question_and_results() -> None:
    with pytest.raises(ValueError, match="question"):
        build_prompt("   ", [RESULT])
    with pytest.raises(ValueError, match="results"):
        build_prompt("a question", [])


# ---------------------------------------------------------------------------
# _call_llm / generate_answer plumbing
# ---------------------------------------------------------------------------


def test_generate_answer_uses_retrieved_context(monkeypatch) -> None:
    def fake_retrieve(question: str, *, conn, bedrock_client, top_k: int, document_id) -> dict:
        return {"question": question.strip(), "results": [RESULT, RESULT2]}

    monkeypatch.setattr(generate, "retrieve", fake_retrieve)
    llm = FakeLLM()

    answer = generate_answer("How many pets?", conn=None, bedrock_client=llm)  # type: ignore[arg-type]

    assert answer["question"] == "How many pets?"
    assert answer["answer"] == "Two pets are allowed."
    assert answer["sources"] == [RESULT, RESULT2]
    assert answer["model_id"] == GENERATION_MODEL_ID

    call = llm.calls[0]
    assert call["modelId"] == GENERATION_MODEL_ID
    assert call["accept"] == "application/json"
    body = json.loads(call["body"])
    assert body["anthropic_version"] == "bedrock-2023-05-31"
    assert body["max_tokens"] == 512
    assert "answer questions about a document collection" in body["system"].lower()
    assert body["messages"] == [
        {"role": "user", "content": build_prompt("How many pets?", [RESULT, RESULT2])}
    ]


def test_generate_answer_skips_llm_when_retrieval_is_empty(monkeypatch) -> None:
    def fake_retrieve(question: str, *, conn, bedrock_client, top_k: int, document_id) -> dict:
        return {"question": question.strip(), "results": []}

    monkeypatch.setattr(generate, "retrieve", fake_retrieve)
    llm = FakeLLM()

    answer = generate_answer("Anything at all?", conn=None, bedrock_client=llm)  # type: ignore[arg-type]

    assert answer["answer"] == "No relevant documents were found for this question."
    assert answer["sources"] == []
    assert llm.calls == []  # no tokens spent on an empty context


def test_generate_answer_forwards_validation_errors() -> None:
    with pytest.raises(ValueError, match="question must not be empty"):
        generate_answer("   ", conn=None, bedrock_client=None)  # type: ignore[arg-type]


def test_call_llm_rejects_empty_content() -> None:
    class EmptyLLM(FakeLLM):
        def invoke_model(self, **kwargs) -> dict:
            self.calls.append(kwargs)
            return {"body": io.BytesIO(b'{"content": []}')}

    with pytest.raises(ValueError, match="no content blocks"):
        generate._call_llm("q", bedrock_client=EmptyLLM(), max_tokens=100, model_id="m")


def test_call_llm_rejects_validation_errors_with_value_error(monkeypatch) -> None:
    monkeypatch.setattr(bedrock_mod.time, "sleep", lambda s: None)

    class RejectingLLM(FakeLLM):
        def invoke_model(self, **kwargs) -> dict:
            self.calls.append(kwargs)
            raise ClientError(
                error_response={"Error": {"Code": "ValidationException", "Message": "too long"}},
                operation_name="InvokeModel",
            )

    llm = RejectingLLM()
    with pytest.raises(ValueError, match="model rejected the request"):
        generate._call_llm("q", bedrock_client=llm, max_tokens=100, model_id="m")
    assert len(llm.calls) == 1  # permanent: never retried


@pytest.mark.parametrize("bad", [0, -1, True, 1.5])
def test_call_llm_validates_max_tokens(bad) -> None:
    with pytest.raises(ValueError, match="max_tokens"):
        generate._call_llm("q", bedrock_client=FakeLLM(), max_tokens=bad, model_id="m")
