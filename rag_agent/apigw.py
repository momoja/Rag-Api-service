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
