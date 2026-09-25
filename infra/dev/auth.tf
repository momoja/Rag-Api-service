# Authentication for the HTTP API (Chapter 12).
#
# Chapter 5 shipped the API with no auth, on the reasoning that its only route
# minted presigned upload URLs. That trigger has now fired: the API also serves
# /documents/search and /documents/answer, which return document text. Every
# route is behind a Cognito JWT authorizer as of this chapter.
#
# Why Cognito + a JWT authorizer:
# - The client is a browser (the API is CORS-enabled and the routes take JSON
#   bodies). Cognito issues tokens a browser can hold; IAM SigV4 would require
#   handing the browser AWS credentials, and an API key is a shared secret with
#   no identity, no expiry and no revocation.
# - API Gateway validates the token — signature, issuer, audience, expiry —
#   before the function is invoked. The check lives at the edge, in one place,
#   and requests without a valid token never reach Lambda (they get 401 from
#   the gateway). There is no session, so nothing to store server-side.
# - Cost is ~$0 at dev scale (the Lite tier covers the first 10k monthly active
#   users).
#
# Scope decisions, deliberately:
# - Admin-created users only (no self sign-up): this is an internal tool until
#   a real client exists, so `allow_admin_create_user_only` is on and the
#   runbook documents creating a dev user. Enabling self-service sign-up is a
#   product decision, not an infrastructure default.
# - No MFA, email-as-username, verified by email. Revisit for prod.
# - The browser holds an ID token as `Authorization: Bearer <token>`; it is the
#   ID token (not the access token) because the JWT authorizer checks the
#   audience claim, which Cognito puts in the ID token.
# - A hosted UI / login page would need a Cognito domain — not created: the
#   token endpoint is enough for a client that renders its own login.

locals {
  cognito_issuer = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.users.id}"
}

resource "aws_cognito_user_pool" "users" {
  name = "${var.project}-${var.environment}-users"

  # Email is the username: one identifier to remember, and it is the claim the
  # handlers log as the caller.
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  password_policy {
    minimum_length    = 12
    require_lowercase = true
    require_uppercase = true
    require_numbers   = true
    require_symbols   = true
  }

  # Cognito's own MFA support stays off for dev; the API's threat model at this
  # stage is "keep strangers out", not "protect against a compromised laptop".
  mfa_configuration = "OFF"
}

resource "aws_cognito_user_pool_client" "app" {
  name         = "${var.project}-${var.environment}-app"
  user_pool_id = aws_cognito_user_pool.users.id

  # Public client: it runs where a secret cannot be kept (browser, CLI).
  # PKCE/SRP is the flow that makes that safe; USER_PASSWORD_AUTH exists for
  # the runbook's token fetch from the AWS CLI.
  generate_secret = false
  explicit_auth_flows = [
    "ALLOW_USER_SRP_AUTH",
    "ALLOW_USER_PASSWORD_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
  ]

  # Do not reveal whether a username exists on a failed sign-in.
  prevent_user_existence_errors = "ENABLED"

  # Short-lived access/ID tokens, long-lived refresh: a stolen token expires,
  # and a real client can stay signed in without re-entering a password.
  access_token_validity  = 1
  id_token_validity      = 1
  refresh_token_validity = 30

  token_validity_units {
    access_token  = "hours"
    id_token      = "hours"
    refresh_token = "days"
  }
}

resource "aws_apigatewayv2_authorizer" "cognito" {
  api_id           = aws_apigatewayv2_api.presign.id
  name             = "${var.project}-${var.environment}-cognito"
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]

  jwt_configuration {
    # audience = the app client: a token minted for a different client in the
    # same pool is refused. issuer = this pool: a token from another pool or a
    # different issuer is refused.
    audience = [aws_cognito_user_pool_client.app.id]
    issuer   = local.cognito_issuer
  }
}
