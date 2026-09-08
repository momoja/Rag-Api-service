"""presign-document Lambda — serves pre-signed S3 PUT URLs over API Gateway.

Chapter 4's direct-invocation contract became an API Gateway proxy event in
Chapter 5: clients call POST /documents/upload-url on the HTTP API and the
gateway forwards a v2 payload-format event whose body is a JSON string:

    event = {
        "version": "2.0",
        "routeKey": "POST /documents/upload-url",
        "body": '{"key": "docs/report.pdf", "expires_in": 900}',
        "isBase64Encoded": false,
        ...
    }

Only matched routes reach the function (the gateway 404s the rest), so no
path/method dispatch lives here. All business logic stays in
rag_agent.storage; this module is thin glue:
parse event -> read env -> call core -> respond.
"""

import base64
import json
import logging
import os

from rag_agent.storage import presign_upload_url

logger = logging.getLogger(__name__)

# Fail fast: missing configuration is a deployment error, not a runtime
# surprise. (Reference repo lesson: os.environ.get() with a silent fallback
# masked a typo'd key and silently used the wrong model.)
DOCUMENTS_BUCKET = os.environ["DOCUMENTS_BUCKET"]


def _respond(status_code: int, payload: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload),
    }


def _parse_body(event: dict) -> dict | None:
    """Decode and JSON-parse the proxy event body.

    Returns the parsed object, or None when the body is absent or malformed
    (caller replies 400). isBase64Encoded is honored for completeness,
    although JSON clients never base64-encode their bodies.
    """
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


def lambda_handler(event: dict, context) -> dict:
    body = _parse_body(event)
    if body is None:
        return _respond(400, {"error": "request body must be a JSON object"})

    try:
        key = body["key"]
    except (KeyError, TypeError):
        return _respond(400, {"error": "missing required field: 'key'"})

    try:
        expires_in = int(body.get("expires_in", 900))
    except (TypeError, ValueError):
        return _respond(400, {"error": "'expires_in' must be an integer"})

    try:
        result = presign_upload_url(DOCUMENTS_BUCKET, key, expires_in=expires_in)
    except ValueError as exc:
        return _respond(400, {"error": str(exc)})
    except Exception:
        logger.exception("presign failed for key=%r", key)
        return _respond(500, {"error": "internal error"})

    return _respond(200, result)
