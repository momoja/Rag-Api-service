"""Tests for the retrieve-document Lambda handler (API Gateway v2 events).

Offline by design: rag_agent.retrieval.retrieve and
rag_agent.generate.generate_answer are patched with fakes and the DB/Bedrock
builders are stubbed, so no client or network is involved. The retrieval and
generation cores are covered in their integration suites against real
pgvector; this suite pins the handler contract — v2 proxy event in, proxy
response out, 400/500 mapping, and routeKey dispatch between the search
(ch9) and answer (ch10) paths.
"""

import base64
import json

import pytest
import retrieve_handler

from rag_agent.retrieval import DEFAULT_TOP_K

ANSWER_BODY = {"question": "q", "answer": "the answer", "sources": [], "model_id": "m"}


def _event(
    body: str | None = None,
    *,
    b64: bool = False,
    route: str = "POST /documents/search",
) -> dict:
    raw_path = "/documents/" + ("answer" if route.endswith("/answer") else "search")
    event: dict = {
        "version": "2.0",
        "routeKey": route,
        "rawPath": raw_path,
        "rawQueryString": "",
        "headers": {"content-type": "application/json"},
        "body": body,
        "isBase64Encoded": b64,
    }
    return event


def _body_of(response: dict) -> dict:
    return json.loads(response["body"])


@pytest.fixture(autouse=True)
def _stub_clients(monkeypatch) -> None:
    """The real DB/Bedrock builders must never run: the handler builds them
    before the (patched) cores. Stub both; _connect is glue covered by its
    own missing-config test below."""
    monkeypatch.setattr(retrieve_handler, "_get_conn", lambda: object())
    monkeypatch.setattr(retrieve_handler, "_get_bedrock", lambda: object())


@pytest.fixture
def fake_retrieve(monkeypatch) -> list[dict]:
    """Replaces retrieve_handler.retrieve; records calls, returns canned
    results."""
    calls: list[dict] = []

    def fake(question: str, *, conn, bedrock_client, top_k: int, document_id) -> dict:
        calls.append(
            {
                "question": question,
                "conn": conn,
                "bedrock_client": bedrock_client,
                "top_k": top_k,
                "document_id": document_id,
            }
        )
        return {
            "question": question,
            "results": [{"document_id": "doc-a", "chunk_index": 0, "text": "hit", "distance": 0.0}],
        }

    monkeypatch.setattr(retrieve_handler, "retrieve", fake)
    return calls


@pytest.fixture
def fake_generate(monkeypatch) -> list[dict]:
    """Replaces retrieve_handler.generate_answer; records calls, returns a
    canned answer."""
    calls: list[dict] = []

    def fake(question: str, *, conn, bedrock_client, top_k: int, document_id) -> dict:
        calls.append(
            {
                "question": question,
                "conn": conn,
                "bedrock_client": bedrock_client,
                "top_k": top_k,
                "document_id": document_id,
            }
        )
        return dict(ANSWER_BODY, question=question)

    monkeypatch.setattr(retrieve_handler, "generate_answer", fake)
    return calls


# --- search route (ch9) ------------------------------------------------------


def test_happy_path_returns_results(fake_retrieve) -> None:
    event = _event(json.dumps({"question": "what is the rent policy?"}))

    response = retrieve_handler.lambda_handler(event, None)

    assert response["statusCode"] == 200
    assert response["headers"]["Content-Type"] == "application/json"
    body = _body_of(response)
    assert body["question"] == "what is the rent policy?"
    assert body["results"][0]["text"] == "hit"
    call = fake_retrieve[0]
    assert call["question"] == "what is the rent policy?"
    assert call["top_k"] == DEFAULT_TOP_K
    assert call["document_id"] is None


def test_top_k_and_document_id_are_forwarded(fake_retrieve) -> None:
    event = _event(json.dumps({"question": "q", "top_k": 3, "document_id": "doc-x"}))

    retrieve_handler.lambda_handler(event, None)

    assert fake_retrieve[0]["top_k"] == 3
    assert fake_retrieve[0]["document_id"] == "doc-x"


def test_base64_encoded_body_is_decoded(fake_retrieve) -> None:
    raw = json.dumps({"question": "b64 question"})
    event = _event(base64.b64encode(raw.encode("utf-8")).decode("ascii"), b64=True)

    response = retrieve_handler.lambda_handler(event, None)

    assert response["statusCode"] == 200
    assert fake_retrieve[0]["question"] == "b64 question"


# --- answer route (ch10) -----------------------------------------------------


def test_answer_route_returns_answer_and_sources(fake_generate) -> None:
    event = _event(json.dumps({"question": "answer me"}), route="POST /documents/answer")

    response = retrieve_handler.lambda_handler(event, None)

    assert response["statusCode"] == 200
    body = _body_of(response)
    assert body["answer"] == "the answer"
    assert body["sources"] == []
    assert body["model_id"] == "m"
    assert fake_generate[0]["question"] == "answer me"
    assert fake_generate[0]["top_k"] == DEFAULT_TOP_K


def test_answer_route_forwards_top_k(fake_generate) -> None:
    event = _event(
        json.dumps({"question": "q", "top_k": 4}),
        route="POST /documents/answer",
    )

    retrieve_handler.lambda_handler(event, None)

    assert fake_generate[0]["top_k"] == 4


def test_answer_route_never_calls_search_core(fake_generate, fake_retrieve) -> None:
    event = _event(json.dumps({"question": "q"}), route="POST /documents/answer")

    retrieve_handler.lambda_handler(event, None)

    assert fake_retrieve == []  # routeKey dispatch picked generate only


def test_search_route_never_calls_answer_core(fake_retrieve, fake_generate) -> None:
    event = _event(json.dumps({"question": "q"}), route="POST /documents/search")

    retrieve_handler.lambda_handler(event, None)

    assert fake_generate == []


def test_answer_core_value_error_maps_to_400(monkeypatch) -> None:
    def boom(question: str, *, conn, bedrock_client, top_k: int, document_id) -> dict:
        raise ValueError("model rejected the request")

    monkeypatch.setattr(retrieve_handler, "generate_answer", boom)

    response = retrieve_handler.lambda_handler(
        _event(json.dumps({"question": "q"}), route="POST /documents/answer"), None
    )

    assert response["statusCode"] == 400
    assert "model rejected" in _body_of(response)["error"]


def test_answer_transient_failure_maps_to_500(monkeypatch) -> None:
    def boom(question: str, *, conn, bedrock_client, top_k: int, document_id) -> dict:
        raise RuntimeError("bedrock throttled")

    monkeypatch.setattr(retrieve_handler, "generate_answer", boom)

    response = retrieve_handler.lambda_handler(
        _event(json.dumps({"question": "q"}), route="POST /documents/answer"), None
    )

    assert response["statusCode"] == 500
    assert _body_of(response)["error"] == "internal error"


# --- shared validation --------------------------------------------------------


@pytest.mark.parametrize(
    "bad_event",
    [
        _event(),  # no body
        _event("not json"),
        _event('"a bare string"'),
        _event("[1, 2]"),
    ],
    ids=["no-body", "not-json", "bare-string", "array"],
)
def test_malformed_body_returns_400(bad_event: dict) -> None:
    response = retrieve_handler.lambda_handler(bad_event, None)

    assert response["statusCode"] == 400
    assert "JSON object" in _body_of(response)["error"]


@pytest.mark.parametrize("payload", [{}, {"top_k": 5}], ids=["empty", "no-question"])
def test_missing_question_returns_400(payload: dict) -> None:
    response = retrieve_handler.lambda_handler(_event(json.dumps(payload)), None)

    assert response["statusCode"] == 400
    assert _body_of(response)["error"] == "missing required field: 'question'"


@pytest.mark.parametrize("bad", ['"abc"', '"12.5"'], ids=["letters", "decimal"])
def test_non_integer_top_k_returns_400(bad: str) -> None:
    event = _event(json.dumps({"question": "q", "top_k": bad}))

    response = retrieve_handler.lambda_handler(event, None)

    assert response["statusCode"] == 400
    assert _body_of(response)["error"] == "'top_k' must be an integer"


def test_core_value_error_maps_to_400(monkeypatch) -> None:
    def boom(question: str, *, conn, bedrock_client, top_k: int, document_id) -> dict:
        raise ValueError("top_k must be an integer in [1, 20]")

    monkeypatch.setattr(retrieve_handler, "retrieve", boom)

    response = retrieve_handler.lambda_handler(
        _event(json.dumps({"question": "q", "top_k": 99})), None
    )

    assert response["statusCode"] == 400
    assert "top_k" in _body_of(response)["error"]


def test_unexpected_core_error_maps_to_500(monkeypatch) -> None:
    def boom(question: str, *, conn, bedrock_client, top_k: int, document_id) -> dict:
        raise RuntimeError("db gone")

    monkeypatch.setattr(retrieve_handler, "retrieve", boom)

    response = retrieve_handler.lambda_handler(_event(json.dumps({"question": "q"})), None)

    assert response["statusCode"] == 500
    assert _body_of(response)["error"] == "internal error"


def test_missing_db_config_fails_loud(monkeypatch) -> None:
    monkeypatch.delenv("DB_DSN", raising=False)
    monkeypatch.delenv("SECRET_ARN", raising=False)
    retrieve_handler._conn = None

    with pytest.raises(RuntimeError, match="DB_DSN or SECRET_ARN"):
        retrieve_handler._connect()
