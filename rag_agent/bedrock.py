import logging
import time

from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

DEFAULT_RETRY_ATTEMPTS = 3
MAX_RETRY_DELAY_SECONDS = 8.0

RETRYABLE_ERROR_CODES = frozenset(
    {
        "ThrottlingException",
        "ModelTimeoutException",
        "ModelErrorException",
        "InternalServerException",
        "ServiceUnavailableException",
    }
)


def is_retryable(exc: Exception) -> bool:
    """True when ``exc`` is a transient Bedrock/network failure."""
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        return code in RETRYABLE_ERROR_CODES
    return False


def retry_call(fn, *, attempts: int = DEFAULT_RETRY_ATTEMPTS, label: str = "bedrock call"):
    if not isinstance(attempts, int) or isinstance(attempts, bool):
        raise ValueError("attempts must be an integer")
    if attempts < 1:
        raise ValueError("attempts must be >= 1")

    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:
            last_error = exc
            if not is_retryable(exc) or attempt == attempts - 1:
                raise
            delay = min(0.5 * (2**attempt), MAX_RETRY_DELAY_SECONDS)
            logger.warning(
                "%s retry %d/%d after %s in %.1fs",
                label,
                attempt + 1,
                attempts,
                type(exc).__name__,
                delay,
            )
            time.sleep(delay)
    raise last_error  # pragma: no cover — the final attempt always raises
