"""presign-document Lambda — Chapter 4's first function.

Returns a pre-signed S3 PUT URL so clients can upload documents straight to
the raw-documents bucket (infra/dev/s3.tf) without credentials or a proxy.

Direct-invocation contract (Chapter 5 wires API Gateway to this shape):
    event = {"key": "path/to/file.pdf", "expires_in": 900}  # expires_in optional

Responses are API Gateway proxy style so Chapter 5 can integrate as-is.
All business logic lives in rag_agent.storage; this module is thin glue:
parse event -> read env -> call core -> respond.
"""

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


def lambda_handler(event: dict, context) -> dict:
    try:
        key = event["key"]
    except (KeyError, TypeError):
        return _respond(400, {"error": "missing required field: 'key'"})

    try:
        expires_in = int(event.get("expires_in", 900))
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
