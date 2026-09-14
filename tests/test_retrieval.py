"""Offline unit tests for rag_agent.retrieval — validation paths only.

Connection + Bedrock paths are exercised in test_retrieval_integration.py
(gated on a reachable pgvector DB); every test here fails validation before
either client is touched, so none are constructed.
"""

import pytest

from rag_agent.retrieval import MAX_QUESTION_LENGTH, retrieve


class ExplodingClient:
    """Any use is a test failure: validation must precede all IO."""

    def __init__(self) -> None:
        raise AssertionError("no client may be built for invalid input")


@pytest.mark.parametrize(
    "bad",
    ["", "   \n\t ", 42, None, "x" * (MAX_QUESTION_LENGTH + 1)],
    ids=["empty", "whitespace", "non-str", "none", "too-long"],
)
def test_retrieve_rejects_bad_questions(bad) -> None:
    with pytest.raises(ValueError, match="question"):
        retrieve(bad, conn=None, bedrock_client=None)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "bad", [0, -1, 21, True, 1.5, "5"], ids=["zero", "neg", "over-max", "bool", "float", "str"]
)
def test_retrieve_rejects_bad_top_k(bad) -> None:
    with pytest.raises(ValueError, match="top_k"):
        retrieve("a question", conn=None, bedrock_client=None, top_k=bad)  # type: ignore[arg-type]


def test_retrieve_rejects_empty_document_id_filter() -> None:
    with pytest.raises(ValueError, match="document_id"):
        retrieve(
            "a question",
            conn=None,  # type: ignore[arg-type]
            bedrock_client=None,  # type: ignore[arg-type]
            document_id="",
        )


def test_retrieve_trims_surrounding_whitespace() -> None:
    """Validation strips the question before length/emptiness checks."""
    with pytest.raises(ValueError, match="question must not be empty"):
        retrieve("   ", conn=None, bedrock_client=None)  # type: ignore[arg-type]
