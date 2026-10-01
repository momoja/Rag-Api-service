# presign-document Lambda 

locals {
  presign_function_name = "${var.project}-${var.environment}-presign-document"
  presign_log_group     = "/aws/lambda/${local.presign_function_name}"
}

resource "aws_ecr_repository" "presign_document" {
  name                 = "${var.project}-${var.environment}/presign-document"
  image_tag_mutability = "MUTABLE" # dev: ':latest' overwritten on each push
  force_delete         = false

  image_scanning_configuration {
    scan_on_push = true
  }
}

data "aws_iam_policy_document" "presign_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "presign_role_policy" {
  # CloudWatch logs, scoped to this function's own log group only.
  statement {
    actions   = ["logs:CreateLogGroup"]
    resources = ["arn:aws:logs:${var.region}:${local.account_id}:log-group:${local.presign_log_group}"]
  }
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.region}:${local.account_id}:log-group:${local.presign_log_group}:log-stream:*"]
  }
  
  statement {
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.documents.arn}/*"]
  }
}

resource "aws_iam_role" "presign_lambda" {
  name               = local.presign_function_name
  assume_role_policy = data.aws_iam_policy_document.presign_assume_role.json
}

resource "aws_iam_role_policy" "presign_lambda" {
  name   = "presign-document"
  role   = aws_iam_role.presign_lambda.id
  policy = data.aws_iam_policy_document.presign_role_policy.json
}

resource "aws_cloudwatch_log_group" "presign_lambda" {
  name              = local.presign_log_group
  retention_in_days = 7
}

resource "aws_lambda_function" "presign_document" {
  function_name = local.presign_function_name
  role          = aws_iam_role.presign_lambda.arn
  image_uri     = "${aws_ecr_repository.presign_document.repository_url}:latest"
  package_type  = "Image"
  timeout       = 30
  memory_size   = 128

  environment {
    variables = {
      DOCUMENTS_BUCKET = aws_s3_bucket.documents.id
    }
  }

  depends_on = [aws_cloudwatch_log_group.presign_lambda]
}
