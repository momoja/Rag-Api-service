import base64
import json
import logging
import os

from rag_agent.apigw import caller_identity
from rag_agent.observability import bind, bind_invocation, configure_logging
from rag_agent.storage import presign_upload_url

logger = logging.getLogger(__name__)

DOCUMENTS_BUCKET = os.environ["DOCUMENTS_BUCKET"]


def _respond(status_code: int, payload: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload),
    }


def _parse_body(event: dict) -> dict | None:
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
    configure_logging()
    bind_invocation(context, stage="presign", route=event.get("routeKey"))
    bind(user=caller_identity(event))
    body = _parse_body(event)
    if body is None:
        return _respond(400, {"error": "request body must be a JSON object"})

    try:
        key = body["key"]
    except (KeyError, TypeError):
        return _respond(400, {"error": "missing required field: 'key'"})
    bind(key=key)

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

    logger.info("presigned upload url issued for key=%s expires_in=%d", key, expires_in)
    return _respond(200, result)
