"""Tests for rag_agent.observability — structured lines and request context.

The context contract matters more than the JSON shape: the pipeline crosses
five functions, so "which document was this line about?" has to be answerable
from any module without threading arguments through every logger call.
"""

import json
import logging
import sys

import pytest

from rag_agent.observability import (
    JsonFormatter,
    bind,
    bind_invocation,
    configure_logging,
    context,
    reset,
)


class FakeLambdaContext:
    """Stands in for the AWS context object (only aws_request_id is read)."""

    aws_request_id = "req-123"


def make_record(**extra) -> logging.LogRecord:
    record = logging.LogRecord(
        name="rag_agent.ingest",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="ingested %s",
        args=("uploads/a.txt",),
        exc_info=None,
    )
    record.__dict__.update(extra)
    return record


def emitted(record: logging.LogRecord) -> dict:
    return json.loads(JsonFormatter().format(record))


@pytest.fixture(autouse=True)
def _clean_context():
    reset()
    yield
    reset()


@pytest.fixture
def root_logger():
    """Restore the root logger's handlers even if a test pokes at them."""
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    yield root
    for handler in list(root.handlers):
        root.removeHandler(handler)
    for handler in handlers:
        root.addHandler(handler)
    root.setLevel(level)


# --- line format -------------------------------------------------------------


def test_line_is_one_json_object_with_core_fields() -> None:
    payload = emitted(make_record())

    assert payload["level"] == "INFO"
    assert payload["logger"] == "rag_agent.ingest"
    assert payload["message"] == "ingested uploads/a.txt"  # %-args are rendered
    assert payload["ts"].startswith("20")
    assert "\n" not in JsonFormatter().format(make_record())


def test_bound_context_rides_on_every_line() -> None:
    bind(stage="ingest", document_id="doc-1", key="uploads/a.txt")

    payload = emitted(make_record())

    assert payload["document_id"] == "doc-1"
    assert payload["stage"] == "ingest"
    assert payload["key"] == "uploads/a.txt"


def test_record_extras_are_rendered() -> None:
    assert emitted(make_record(num_chunks=3))["num_chunks"] == 3


def test_exception_is_rendered_as_text_not_repr() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        record = make_record(exc_info=sys.exc_info())

    assert "ValueError: boom" in emitted(record)["exception"]


# --- context handling --------------------------------------------------------


def test_bind_merges_and_drops_empty_values() -> None:
    bind(stage="ingest")
    bind(document_id="doc-1")
    assert context() == {"stage": "ingest", "document_id": "doc-1"}

    bind(document_id=None)  # None means "no value": it neither sets nor clears
    assert context() == {"stage": "ingest", "document_id": "doc-1"}


def test_context_is_a_snapshot_not_a_handle() -> None:
    bind(stage="ingest")

    context()["stage"] = "tampered"

    assert context() == {"stage": "ingest"}


def test_bind_invocation_resets_the_previous_invocation() -> None:
    bind(document_id="stale")

    bind_invocation(FakeLambdaContext(), stage="embed")

    assert context() == {"stage": "embed", "request_id": "req-123"}


def test_bind_invocation_outside_lambda_omits_request_id() -> None:
    bind_invocation(None, stage="presign")

    assert context() == {"stage": "presign"}


# --- installation ------------------------------------------------------------


def test_configure_logging_adds_one_json_handler_and_is_idempotent(root_logger) -> None:
    configure_logging()
    configure_logging()

    json_handlers = [h for h in root_logger.handlers if isinstance(h.formatter, JsonFormatter)]
    assert len(json_handlers) == 1
    assert root_logger.level <= logging.INFO


def test_configure_logging_keeps_a_more_verbose_level(root_logger) -> None:
    root_logger.setLevel(logging.DEBUG)

    configure_logging()

    assert root_logger.level == logging.DEBUG


# --- handler wiring ----------------------------------------------------------


def test_ingest_handler_binds_stage_key_and_document(monkeypatch) -> None:
    """The pipeline handlers must stamp their own stage and document id."""
    import ingest_handler

    monkeypatch.setattr(
        ingest_handler,
        "process_document",
        lambda *args, **kwargs: {"document_id": "doc-9", "num_chunks": 2},
    )

    response = ingest_handler.lambda_handler(
        {
            "Records": [
                {
                    "eventName": "ObjectCreated:Put",
                    "s3": {
                        "bucket": {"name": "rag-agent-dev-documents-0"},
                        "object": {"key": "uploads/a.txt", "versionId": "v1"},
                    },
                }
            ]
        },
        FakeLambdaContext(),
    )

    assert response["processed"][0]["document_id"] == "doc-9"
    assert context() == {
        "stage": "ingest",
        "request_id": "req-123",
        "key": "uploads/a.txt",
        "version_id": "v1",
        "document_id": "doc-9",
        "num_chunks": 2,
    }


def test_retrieve_handler_binds_route_and_result_count(monkeypatch) -> None:
    import retrieve_handler

    monkeypatch.setattr(
        retrieve_handler,
        "retrieve",
        lambda *args, **kwargs: {"question": "q", "results": [{"document_id": "d"}]},
    )
    monkeypatch.setattr(retrieve_handler, "_get_conn", lambda: object())
    monkeypatch.setattr(retrieve_handler, "_get_bedrock", lambda: object())

    response = retrieve_handler.lambda_handler(
        {
            "routeKey": "POST /documents/search",
            "body": json.dumps({"question": "q", "top_k": 3}),
            "requestContext": {
                "authorizer": {"jwt": {"claims": {"email": "dev@example.com", "sub": "abc"}}}
            },
        },
        FakeLambdaContext(),
    )

    assert response["statusCode"] == 200
    assert context() == {
        "stage": "search",
        "route": "POST /documents/search",
        "request_id": "req-123",
        "user": "dev@example.com",  # the gateway's verified claims reach the logs
        "top_k": 3,
        "num_results": 1,
    }


def test_presign_handler_binds_the_authenticated_caller(monkeypatch) -> None:
    import handler

    monkeypatch.setattr(
        handler,
        "presign_upload_url",
        lambda *args, **kwargs: {"url": "https://s3.example/put", "key": "uploads/a.txt"},
    )

    response = handler.lambda_handler(
        {
            "routeKey": "POST /documents/upload-url",
            "body": json.dumps({"key": "uploads/a.txt"}),
            "requestContext": {
                "authorizer": {"jwt": {"claims": {"email": "dev@example.com", "sub": "abc"}}}
            },
        },
        FakeLambdaContext(),
    )

    assert response["statusCode"] == 200
    assert context()["user"] == "dev@example.com"
