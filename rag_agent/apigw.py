"""API Gateway request helpers (Chapter 12).

Authentication is enforced by the HTTP API's JWT authorizer, not here: API
Gateway validates the Cognito ID token's signature, issuer, audience and
expiry before the function is invoked, and a request without a valid token
never reaches Lambda (it gets 401 from the gateway). Nothing in this module
re-validates a token — that would duplicate the check and need a JWKS cache to
maintain, and no invocation path bypasses the gateway.

What the functions do need is *who* asked. The gateway hands the verified
claims to the function in the v2 event's ``requestContext.authorizer.jwt.
claims``; these helpers read them defensively, because a direct
``aws lambda invoke`` — the DLQ replay path in the runbook — carries no
authorizer section at all, and a missing claim must degrade to "unknown
caller", never to a crash.
"""


def jwt_claims(event: dict) -> dict:
    """Verified JWT claims from an API Gateway v2 event, or {} when absent."""
    if not isinstance(event, dict):
        return {}
    request_context = event.get("requestContext")
    if not isinstance(request_context, dict):
        return {}
    authorizer = request_context.get("authorizer")
    if not isinstance(authorizer, dict):
        return {}
    jwt = authorizer.get("jwt")
    if not isinstance(jwt, dict):
        return {}
    claims = jwt.get("claims")
    return claims if isinstance(claims, dict) else {}


def caller_identity(event: dict) -> str | None:
    """Human-readable caller identity: email, else username, else subject.

    Returns None when the event carries no claims (direct invocation), which
    the log context drops rather than rendering as a null field.
    """
    claims = jwt_claims(event)
    for key in ("email", "cognito:username", "username", "sub"):
        value = claims.get(key)
        if isinstance(value, str) and value:
            return value
    return None
