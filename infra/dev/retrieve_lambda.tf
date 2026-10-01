
locals {
  retrieve_function_name = "${var.project}-${var.environment}-retrieve-document"
  retrieve_log_group     = "/aws/lambda/${local.retrieve_function_name}"
}

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
  
  statement {
    actions   = ["logs:CreateLogGroup"]
    resources = ["arn:aws:logs:${var.region}:${local.account_id}:log-group:${local.retrieve_log_group}"]
  }
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.region}:${local.account_id}:log-group:${local.retrieve_log_group}:log-stream:*"]
  }
   
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.index_db.arn]
  }
   
  statement {
    actions   = ["bedrock:InvokeModel"]
    resources = ["arn:aws:bedrock:${var.region}::foundation-model/amazon.titan-embed-text-v2:0"]
  }
  
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

  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_apigatewayv2_route" "answer" {
  api_id    = aws_apigatewayv2_api.presign.id
  route_key = "POST /documents/answer"
  target    = "integrations/${aws_apigatewayv2_integration.retrieve.id}"

  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_lambda_permission" "retrieve_apigateway" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.retrieve_document.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.presign.execution_arn}/*/*"
}
