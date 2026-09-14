"""Tests for rag_agent.bedrock — the shared retry convention.

Embed-specific retry behavior is covered through embed_texts in
test_embed.py; this suite pins retry_call itself (the loop both Bedrock
consumers share). Sleeps are patched so the suite stays fast.
"""

import pytest
from botocore.exceptions import ClientError

import rag_agent.bedrock as bedrock


def client_error(code: str) -> ClientError:
    return ClientError(
        error_response={"Error": {"Code": code, "Message": "boom"}},
        operation_name="InvokeModel",
    )


def test_retry_call_recovers_after_transient_failures(monkeypatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr(bedrock.time, "sleep", slept.append)
    calls = {"n": 0}

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise client_error("ThrottlingException")
        return "ok"

    assert bedrock.retry_call(flaky, attempts=5, label="test") == "ok"
    assert calls["n"] == 3
    assert len(slept) == 2
    assert slept[0] < slept[1]  # exponential backoff grows


def test_retry_call_exhaustion_raises_last_error(monkeypatch) -> None:
    monkeypatch.setattr(bedrock.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def always_throttled() -> None:
        calls["n"] += 1
        raise client_error("ThrottlingException")

    with pytest.raises(ClientError, match="Throttling"):
        bedrock.retry_call(always_throttled, attempts=2, label="test")

    assert calls["n"] == 2  # bounded: never more than attempts


def test_retry_call_permanent_errors_are_not_retried(monkeypatch) -> None:
    monkeypatch.setattr(bedrock.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def access_denied() -> None:
        calls["n"] += 1
        raise client_error("AccessDeniedException")

    with pytest.raises(ClientError, match="AccessDenied"):
        bedrock.retry_call(access_denied, attempts=3, label="test")

    assert calls["n"] == 1  # misconfiguration fails loud, never retried


@pytest.mark.parametrize("bad", [0, -1, True, 1.5])
def test_retry_call_validates_attempts(bad) -> None:
    with pytest.raises(ValueError, match="attempts"):
        bedrock.retry_call(lambda: None, attempts=bad)


def test_retry_call_single_attempt_never_sleeps(monkeypatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr(bedrock.time, "sleep", slept.append)

    with pytest.raises(ClientError):
        bedrock.retry_call(
            lambda: (_ for _ in ()).throw(client_error("ThrottlingException")), attempts=1
        )

    assert slept == []
