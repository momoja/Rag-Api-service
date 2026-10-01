

locals {
  presign_api_name = "${var.project}-${var.environment}-api"
}

resource "aws_apigatewayv2_api" "presign" {
  name          = local.presign_api_name
  protocol_type = "HTTP"
  description   = "RAG agent API (${var.environment})"


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

  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.presign.id
  name        = "$default"
  auto_deploy = true
}
 
resource "aws_lambda_permission" "presign_apigateway" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.presign_document.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.presign.execution_arn}/*/*"
}
