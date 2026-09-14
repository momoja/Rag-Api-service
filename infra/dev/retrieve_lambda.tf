# retrieve-document Lambda + API route (Chapter 9) — the retrieval endpoint.
#
# POST /documents/search on the Chapter 5 HTTP API: embeds the question with
# Bedrock Titan (same model as ingest), searches rag_chunks (pgvector, RDS),
# returns top-K ranked chunks with text. No LLM — Chapter 10 adds generation
# over this same route/function shape.
#
# Like index-document, this function is VPC-attached (RDS is private) and
# reads its DB credentials from Secrets Manager (SECRET_ARN). Bedrock is
# reached through the bedrock-runtime VPC endpoint (vpc_endpoints.tf).
# Shared data blocks (aws_vpc.default / aws_subnets.default) live in
# vector_store.tf and are referenced, not redefined.

locals {
  retrieve_function_name = "${var.project}-${var.environment}-retrieve-document"
  retrieve_log_group     = "/aws/lambda/${local.retrieve_function_name}"
}

# VPC traffic is allowed by endpoints/SGs only (see vpc_endpoints.tf).
resource "aws_security_group" "retrieve_lambda" {
  name        = "${var.project}-${var.environment}-retrieve-lambda"
  description = "Egress for retrieve-document Lambda (VPC-attached)"
  vpc_id      = data.aws_vpc.default.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_ecr_repository" "retrieve_document" {
  name                 = "${var.project}-${var.environment}/retrieve-document"
  image_tag_mutability = "MUTABLE"
  force_delete         = false

  image_scanning_configuration {
    scan_on_push = true
  }
}

data "aws_iam_policy_document" "retrieve_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "retrieve_role_policy" {
  # CloudWatch logs, scoped to this function's own log group only.
  statement {
    actions   = ["logs:CreateLogGroup"]
    resources = ["arn:aws:logs:${var.region}:${local.account_id}:log-group:${local.retrieve_log_group}"]
  }
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.region}:${local.account_id}:log-group:${local.retrieve_log_group}:log-stream:*"]
  }
  # DB credentials at startup (same secret the index function reads).
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.index_db.arn]
  }
  # Query embedding, same model contract as ingest (decision 5/14).
  statement {
    actions   = ["bedrock:InvokeModel"]
    resources = ["arn:aws:bedrock:${var.region}::foundation-model/amazon.titan-embed-text-v2:0"]
  }
  # Answer generation (ch10): Claude, invoked through the same API. A model
  # family change means updating this ARN and GENERATION_MODEL_ID together
  # (without this permission the /documents/answer route fails AccessDenied).
  statement {
    actions   = ["bedrock:InvokeModel"]
    resources = ["arn:aws:bedrock:${var.region}::foundation-model/anthropic.claude-3-5-haiku-20241022-v1:0"]
  }
}

resource "aws_iam_role" "retrieve_lambda" {
  name               = local.retrieve_function_name
  assume_role_policy = data.aws_iam_policy_document.retrieve_assume_role.json
}

resource "aws_iam_role_policy" "retrieve_lambda" {
  name   = "retrieve-document"
  role   = aws_iam_role.retrieve_lambda.id
  policy = data.aws_iam_policy_document.retrieve_role_policy.json
}

resource "aws_iam_role_policy_attachment" "retrieve_lambda_vpc" {
  role       = aws_iam_role.retrieve_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_cloudwatch_log_group" "retrieve_lambda" {
  name              = local.retrieve_log_group
  retention_in_days = 7
}

resource "aws_lambda_function" "retrieve_document" {
  function_name = local.retrieve_function_name
  role          = aws_iam_role.retrieve_lambda.arn
  image_uri     = "${aws_ecr_repository.retrieve_document.repository_url}:latest"
  package_type  = "Image"
  timeout       = 60
  memory_size   = 256

  vpc_config {
    subnet_ids         = data.aws_subnets.default.ids
    security_group_ids = [aws_security_group.retrieve_lambda.id]
  }

  environment {
    variables = {
      SECRET_ARN = aws_secretsmanager_secret.index_db.arn
    }
  }

  depends_on = [aws_cloudwatch_log_group.retrieve_lambda]
}

# --- API Gateway wiring (extends the Chapter 5 HTTP API) ---------------------

resource "aws_apigatewayv2_integration" "retrieve" {
  api_id                 = aws_apigatewayv2_api.presign.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.retrieve_document.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "retrieve" {
  api_id    = aws_apigatewayv2_api.presign.id
  route_key = "POST /documents/search"
  target    = "integrations/${aws_apigatewayv2_integration.retrieve.id}"
}

# Answer route (Chapter 10): same function/integration — the handler
# dispatches on routeKey (search vs generate) and both cores share the
# connection and Bedrock client. The route permission above (/*/*) already
# covers it.
resource "aws_apigatewayv2_route" "answer" {
  api_id    = aws_apigatewayv2_api.presign.id
  route_key = "POST /documents/answer"
  target    = "integrations/${aws_apigatewayv2_integration.retrieve.id}"
}

# Same shape as the presign permission: this API may invoke the function.
resource "aws_lambda_permission" "retrieve_apigateway" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.retrieve_document.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.presign.execution_arn}/*/*"
}
