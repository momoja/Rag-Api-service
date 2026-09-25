# HTTP API in front of presign-document (Chapter 5).
#
# One route at this stage: POST /documents/upload-url -> presign Lambda.
# Chosen over REST API (apigateway): cheaper, simpler, native Lambda proxy
# with payload format v2, built-in CORS config.
#
# Every route now requires a Cognito ID token: auth.tf (ch12) attaches a JWT
# authorizer to this route and to the two query routes in retrieve_lambda.tf.
# Chapter 5's "no auth yet" reasoning expired when search/answer shipped —
# they return document text, and this one mints write access to the bucket.
#
# auto_deploy on the $default stage means re-applying after an image push
# publishes the new function code with no separate deployment step.

locals {
  presign_api_name = "${var.project}-${var.environment}-api"
}

resource "aws_apigatewayv2_api" "presign" {
  name          = local.presign_api_name
  protocol_type = "HTTP"
  description   = "RAG agent API (${var.environment})"

  # Dev-permissive: browsers may call the API from anywhere. Tightened when a
  # real client origin exists. API GW injects CORS headers, so the Lambda
  # handler does not need to.
  cors_configuration {
    allow_origins = ["*"]
    allow_methods = ["POST", "OPTIONS"]
    allow_headers = ["content-type", "authorization"]
    max_age       = 3600
  }
}

resource "aws_apigatewayv2_integration" "presign" {
  api_id                 = aws_apigatewayv2_api.presign.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.presign_document.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "presign" {
  api_id    = aws_apigatewayv2_api.presign.id
  route_key = "POST /documents/upload-url"
  target    = "integrations/${aws_apigatewayv2_integration.presign.id}"

  # Authorization is per route on HTTP APIs, so each route opts in explicitly
  # (the other two are in retrieve_lambda.tf).
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.presign.id
  name        = "$default"
  auto_deploy = true
}

# Without this, API Gateway is not allowed to invoke the function and every
# request 403s. /*/* covers every route on every stage of this API.
resource "aws_lambda_permission" "presign_apigateway" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.presign_document.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.presign.execution_arn}/*/*"
}
