"""Structured logging with per-invocation context (Chapter 12).

CloudWatch Logs is the Lambdas' only observability surface, and the pipeline
crosses five functions: one upload becomes an S3 event, then a chunks file, an
embeddings file, a table row, and finally an HTTP answer. Plain text lines make
that journey hard to follow — you cannot ask "show me everything about document
X" across five log streams when the identifier is buried in prose.

This module emits one JSON object per line and carries per-invocation context
(stage, Lambda request id, document id, S3 key, route) into every record from
every module through a ContextVar, so call sites just log normally:

    logger.info("ingested %s -> %s", key, chunks_key)

renders as

    {"ts": "...", "level": "INFO", "logger": "rag_agent.ingest",
     "message": "ingested uploads/handbook.txt -> processed/abc/chunks.jsonl",
     "stage": "ingest", "document_id": "abc", "request_id": "6f2..."}

which CloudWatch Logs Insights can group and filter on (`filter document_id =
"abc"`), and a metric filter can promote to its own alarm.

Deliberate choices:
- stdout is the transport: the Lambda runtime ships stdout/stderr to
  CloudWatch, so a plain StreamHandler is sufficient.
- ``configure_logging`` ADDS our handler and never removes one it did not
  install. Replacing root handlers is a common way to silently destroy test
  capture (pytest's caplog) and to lose whatever the runtime installed. It is
  called at handler entry, never at import, so importing a handler module has
  no logging side effects.
- Fields with no value are omitted rather than rendered as null.
- ``bind_invocation`` resets the context first: Lambda reuses the execution
  environment across events, so a document id bound by the previous invocation
  would otherwise leak into the next one's lines.
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

_context: ContextVar[dict[str, Any] | None] = ContextVar("rag_log_context", default=None)


def _current() -> dict[str, Any]:
    """Bound context for this invocation/thread (empty until the first bind)."""
    return _context.get() or {}


# LogRecord attributes owned by the stdlib: never re-rendered as payload.
_STANDARD_ATTRS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


def bind(**fields: Any) -> None:
    """Merge ``fields`` into the current log context (None values are dropped)."""
    merged = dict(_current())
    merged.update({key: value for key, value in fields.items() if value is not None})
    _context.set(merged)


def reset() -> None:
    """Clear the log context."""
    _context.set(None)


def context() -> dict[str, Any]:
    """Snapshot of the current log context."""
    return dict(_current())


def bind_invocation(lambda_context: Any, **fields: Any) -> None:
    """Start a fresh log context for one Lambda invocation.

    ``lambda_context`` is the AWS context object (only ``aws_request_id`` is
    read) and may be None outside Lambda.
    """
    reset()
    bind(request_id=getattr(lambda_context, "aws_request_id", None), **fields)


class JsonFormatter(logging.Formatter):
    """Render a LogRecord as one JSON line, merging in the bound context."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(_current())
        payload.update(
            {key: value for key, value in record.__dict__.items() if key not in _STANDARD_ATTRS}
        )
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    """Install the JSON handler on the root logger. Idempotent."""
    root = logging.getLogger()
    if any(isinstance(handler.formatter, JsonFormatter) for handler in root.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    # Keep a more verbose root level (a local DEBUG run); raise a quieter one.
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)
