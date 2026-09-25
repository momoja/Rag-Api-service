"""Tests for rag_agent.apigw — reading the gateway's verified JWT claims.

The authorizer itself is infrastructure (infra/dev/auth.tf), so these tests pin
the contract the handlers depend on: the API Gateway v2 claims shape, and the
no-authorizer case that a direct invocation — the runbook's DLQ replay path —
produces.
"""

import pytest

from rag_agent.apigw import caller_identity, jwt_claims


def event_with_claims(claims) -> dict:
    return {"requestContext": {"authorizer": {"jwt": {"claims": claims}}}}


def test_claims_are_read_from_the_v2_event() -> None:
    claims = {"sub": "abc", "email": "dev@example.com", "cognito:username": "dev@example.com"}

    assert jwt_claims(event_with_claims(claims)) == claims


@pytest.mark.parametrize(
    "event",
    [
        None,
        {},
        {"requestContext": None},
        {"requestContext": {}},
        {"requestContext": {"authorizer": {}}},
        {"requestContext": {"authorizer": {"jwt": {}}}},
        {"requestContext": {"authorizer": {"jwt": {"claims": None}}}},
        {"requestContext": {"authorizer": {"jwt": {"claims": ["not", "a", "dict"]}}}},
        {"Records": []},  # an S3 event: pipeline invocations carry no authorizer
    ],
    ids=[
        "not-a-dict",
        "empty",
        "null-context",
        "no-authorizer",
        "no-jwt",
        "no-claims",
        "null-claims",
        "list-claims",
        "s3-event",
    ],
)
def test_missing_or_malformed_claims_degrade_to_empty(event) -> None:
    assert jwt_claims(event) == {}


def test_caller_identity_prefers_email_then_username_then_subject() -> None:
    assert caller_identity(event_with_claims({"email": "a@b.c", "sub": "s"})) == "a@b.c"
    assert caller_identity(event_with_claims({"cognito:username": "u", "sub": "s"})) == "u"
    assert caller_identity(event_with_claims({"sub": "s"})) == "s"


def test_caller_identity_is_none_without_a_usable_claim() -> None:
    assert caller_identity({}) is None
    assert caller_identity(event_with_claims({"email": ""})) is None
    assert caller_identity(event_with_claims({"email": 42})) is None
