import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

_context: ContextVar[dict[str, Any] | None] = ContextVar("rag_log_context", default=None)


def _current() -> dict[str, Any]:
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
    merged = dict(_current())
    merged.update({key: value for key, value in fields.items() if value is not None})
    _context.set(merged)


def reset() -> None:
    _context.set(None)


def context() -> dict[str, Any]:
    return dict(_current())


def bind_invocation(lambda_context: Any, **fields: Any) -> None:
    reset()
    bind(request_id=getattr(lambda_context, "aws_request_id", None), **fields)


class JsonFormatter(logging.Formatter):

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
    
    root = logging.getLogger()
    if any(isinstance(handler.formatter, JsonFormatter) for handler in root.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    # Keep a more verbose root level (a local DEBUG run); raise a quieter one.
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)
