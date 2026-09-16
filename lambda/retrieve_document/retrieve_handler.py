"""retrieve-document Lambda — the query path over API Gateway (ch9 + ch10).

Serves two routes on the Chapter 5 HTTP API, both as v2 proxy events with
JSON bodies ``{"question": "...", "top_k": 5, "document_id": "..."}``
(top_k/document_id optional):
- POST /documents/search -> rag_agent.retrieval.retrieve: top-K chunks.
- POST /documents/answer -> rag_agent.generate.generate_answer: retrieval
  plus the Chapter 10 LLM stage (answer + cited sources). Same function
  and image for both: the route branches here on routeKey, and both cores
  share the injected DB connection and Bedrock client.

Thin glue: parse event -> connect (DB via DB_DSN or SECRET_ARN, Bedrock
via the default client) -> call the core -> respond. Client-correctable
problems (missing/bad question, top_k bounds) surface as 400s; transient
DB/Bedrock failures as 500s so the client can retry.
"""

import base64
import json
import logging
import os
import time

import psycopg

from rag_agent.generate import generate_answer
from rag_agent.observability import bind, bind_invocation, configure_logging
from rag_agent.retrieval import DEFAULT_TOP_K, retrieve

logger = logging.getLogger(__name__)

_conn = None
_bedrock = None


def _respond(status_code: int, payload: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload),
    }


def _parse_body(event: dict) -> dict | None:
    """Decode and JSON-parse the proxy event body (mirrors presign)."""
    raw = event.get("body")
    if raw is None:
        return None
    if event.get("isBase64Encoded"):
        try:
            raw = base64.b64decode(raw).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _connect():
    """Build a psycopg connection from DB_DSN (dev) or SECRET_ARN (prod RDS).

    Split from _get_conn so tests can exercise the builder while stubbing
    the cached accessor.
    """
    dsn = os.environ.get("DB_DSN")
    if dsn:
        return psycopg.connect(dsn, connect_timeout=10)
    secret_arn = os.environ.get("SECRET_ARN")
    if not secret_arn:
        raise RuntimeError("retrieve-document requires DB_DSN or SECRET_ARN")
    import boto3

    secret = json.loads(
        boto3.client("secretsmanager").get_secret_value(SecretId=secret_arn)["SecretString"]
    )
    return psycopg.connect(
        host=secret["host"],
        port=int(secret.get("port", 5432)),
        dbname=secret["dbname"],
        user=secret["username"],
        password=secret["password"],
        connect_timeout=10,
    )


def _get_conn():
    """One connection per invocation, reused across the cold start."""
    global _conn
    if _conn is None:
        _conn = _connect()
    return _conn


def _get_bedrock():
    """Lazily build the default Bedrock Runtime client (first call only)."""
    global _bedrock
    if _bedrock is None:
        import boto3

        _bedrock = boto3.client("bedrock-runtime")
    return _bedrock


def _is_answer_route(event: dict) -> bool:
    return str(event.get("routeKey", "")).endswith("/documents/answer")


def _bad_request(message: str) -> dict:
    """Reject a client-correctable request, leaving a trace in the logs."""
    logger.warning("rejected request: %s", message)
    return _respond(400, {"error": message})


def lambda_handler(event: dict, context) -> dict:
    """Time one request, log its outcome, and dispatch to the route handler.

    One line per request carries status, duration, and whatever the core
    bound (top_k, document scope, result count) — the query path's whole
    observability story in a single filterable record.
    """
    configure_logging()
    bind_invocation(
        context,
        stage="answer" if _is_answer_route(event) else "search",
        route=event.get("routeKey"),
    )
    started = time.perf_counter()
    response = _handle(event)
    logger.info(
        "query request complete: status=%s duration_ms=%.1f",
        response["statusCode"],
        (time.perf_counter() - started) * 1000,
    )
    return response


def _handle(event: dict) -> dict:
    body = _parse_body(event)
    if body is None:
        return _bad_request("request body must be a JSON object")

    try:
        question = body["question"]
    except (KeyError, TypeError):
        return _bad_request("missing required field: 'question'")

    try:
        top_k = int(body.get("top_k", DEFAULT_TOP_K))
    except (TypeError, ValueError):
        return _bad_request("'top_k' must be an integer")
    document_id = body.get("document_id")
    bind(top_k=top_k, document_id=document_id)

    try:
        if _is_answer_route(event):
            result = generate_answer(
                question,
                conn=_get_conn(),
                bedrock_client=_get_bedrock(),
                top_k=top_k,
                document_id=document_id,
            )
            bind(num_results=len(result["sources"]), answer_chars=len(result["answer"]))
        else:
            result = retrieve(
                question,
                conn=_get_conn(),
                bedrock_client=_get_bedrock(),
                top_k=top_k,
                document_id=document_id,
            )
            bind(num_results=len(result["results"]))
    except ValueError as exc:
        return _bad_request(str(exc))
    except Exception:
        logger.exception("query failed for question=%r", question)
        return _respond(500, {"error": "internal error"})

    return _respond(200, result)
