"""Tests for the presign-document Lambda handler (API Gateway v2 proxy events).

Offline by design: the handler's presign call is patched with a fake, so no
boto3 client or credentials are involved. URL signing itself is covered in
test_storage.py against a fake S3 client; this suite pins the Chapter 5
contract — v2 proxy event in, proxy response out, error mapping.
"""

import base64
import json

import handler
import pytest

from rag_agent.storage import DEFAULT_EXPIRES_SECONDS

FAKE_URL = "https://example.invalid/presigned"


def _event(body: str | None = None, *, b64: bool = False) -> dict:
    event: dict = {
        "version": "2.0",
        "routeKey": "POST /documents/upload-url",
        "rawPath": "/documents/upload-url",
        "rawQueryString": "",
        "headers": {"content-type": "application/json"},
        "body": body,
        "isBase64Encoded": b64,
    }
    return event


@pytest.fixture
def fake_presign(monkeypatch) -> list[tuple[str, str, int]]:
    """Replaces handler.presign_upload_url; records calls, returns canned URL."""
    calls: list[tuple[str, str, int]] = []

    def fake(bucket: str, key: str, *, expires_in: int = DEFAULT_EXPIRES_SECONDS) -> dict:
        calls.append((bucket, key, expires_in))
        return {
            "method": "PUT",
            "bucket": bucket,
            "key": key,
            "url": FAKE_URL,
            "expires_in": expires_in,
        }

    monkeypatch.setattr(handler, "presign_upload_url", fake)
    return calls


def _body_of(response: dict) -> dict:
    return json.loads(response["body"])


def test_happy_path_returns_presign_contract(fake_presign) -> None:
    event = _event(json.dumps({"key": "uploads/docs/report.pdf"}))

    response = handler.lambda_handler(event, None)

    assert response["statusCode"] == 200
    assert response["headers"]["Content-Type"] == "application/json"
    body = _body_of(response)
    assert body["method"] == "PUT"
    assert body["key"] == "uploads/docs/report.pdf"
    assert body["url"] == FAKE_URL
    assert body["expires_in"] == DEFAULT_EXPIRES_SECONDS
    assert fake_presign == [
        (handler.DOCUMENTS_BUCKET, "uploads/docs/report.pdf", DEFAULT_EXPIRES_SECONDS)
    ]


def test_custom_expires_in_is_forwarded(fake_presign) -> None:
    event = _event(json.dumps({"key": "a.pdf", "expires_in": 120}))

    response = handler.lambda_handler(event, None)

    assert response["statusCode"] == 200
    assert _body_of(response)["expires_in"] == 120
    assert fake_presign[0][2] == 120


def test_base64_encoded_body_is_decoded(fake_presign) -> None:
    raw = json.dumps({"key": "uploads/b64.pdf"})
    event = _event(base64.b64encode(raw.encode("utf-8")).decode("ascii"), b64=True)

    response = handler.lambda_handler(event, None)

    assert response["statusCode"] == 200
    assert _body_of(response)["key"] == "uploads/b64.pdf"


@pytest.mark.parametrize(
    "bad_event",
    [
        _event(),  # no body
        _event("not json"),
        _event('"a bare string"'),
        _event("[1, 2]"),
        _event("null"),
    ],
    ids=["no-body", "not-json", "bare-string", "array", "null"],
)
def test_malformed_body_returns_400(bad_event: dict) -> None:
    response = handler.lambda_handler(bad_event, None)

    assert response["statusCode"] == 400
    assert "JSON object" in _body_of(response)["error"]


def test_invalid_base64_returns_400() -> None:
    response = handler.lambda_handler(_event("!!!not-base64!!!", b64=True), None)

    assert response["statusCode"] == 400


@pytest.mark.parametrize("payload", [{}, {"expires_in": 60}], ids=["empty", "no-key"])
def test_missing_key_returns_400(payload: dict) -> None:
    response = handler.lambda_handler(_event(json.dumps(payload)), None)

    assert response["statusCode"] == 400
    assert _body_of(response)["error"] == "missing required field: 'key'"


@pytest.mark.parametrize("bad", ['"abc"', '"12.5"'], ids=["letters", "decimal"])
def test_non_integer_expires_in_returns_400(bad: str) -> None:
    event = _event(json.dumps({"key": "uploads/a.pdf", "expires_in": bad}))

    response = handler.lambda_handler(event, None)

    assert response["statusCode"] == 400
    assert _body_of(response)["error"] == "'expires_in' must be an integer"


def test_core_value_error_maps_to_400(monkeypatch) -> None:
    def boom(bucket: str, key: str, *, expires_in: int) -> dict:
        raise ValueError("key must not contain '..' path segments")

    monkeypatch.setattr(handler, "presign_upload_url", boom)
    event = _event(json.dumps({"key": "../escape.pdf"}))

    response = handler.lambda_handler(event, None)

    assert response["statusCode"] == 400
    assert "path segments" in _body_of(response)["error"]


def test_unexpected_core_error_maps_to_500(monkeypatch) -> None:
    def boom(bucket: str, key: str, *, expires_in: int) -> dict:
        raise RuntimeError("network gone")

    monkeypatch.setattr(handler, "presign_upload_url", boom)
    event = _event(json.dumps({"key": "a.pdf"}))

    response = handler.lambda_handler(event, None)

    assert response["statusCode"] == 500
    assert _body_of(response)["error"] == "internal error"
