
locals {
  embed_function_name = "${var.project}-${var.environment}-embed-document"
  embed_log_group     = "/aws/lambda/${local.embed_function_name}"
}

resource "aws_ecr_repository" "embed_document" {
  name                 = "${var.project}-${var.environment}/embed-document"
  image_tag_mutability = "MUTABLE" # dev: ':latest' overwritten on each push
  force_delete         = false

  image_scanning_configuration {
    scan_on_push = true
  }
}

data "aws_iam_policy_document" "embed_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "embed_role_policy" {
  # CloudWatch logs, scoped to this function's own log group only.
  statement {
    actions   = ["logs:CreateLogGroup"]
    resources = ["arn:aws:logs:${var.region}:${local.account_id}:log-group:${local.embed_log_group}"]
  }
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.region}:${local.account_id}:log-group:${local.embed_log_group}:log-stream:*"]
  }
 
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.documents.arn}/processed/*"]
  }
   
  statement {
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.documents.arn}/embedded/*"]
  }
  
  statement {
    actions   = ["bedrock:InvokeModel"]
    resources = ["arn:aws:bedrock:${var.region}::foundation-model/amazon.titan-embed-text-v2:0"]
  }
 
  statement {
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.dead_letter["embed"].arn]
  }
}

resource "aws_iam_role" "embed_lambda" {
  name               = local.embed_function_name
  assume_role_policy = data.aws_iam_policy_document.embed_assume_role.json
}

resource "aws_iam_role_policy" "embed_lambda" {
  name   = "embed-document"
  role   = aws_iam_role.embed_lambda.id
  policy = data.aws_iam_policy_document.embed_role_policy.json
}

resource "aws_cloudwatch_log_group" "embed_lambda" {
  name              = local.embed_log_group
  retention_in_days = 7
}

resource "aws_lambda_function" "embed_document" {
  function_name = local.embed_function_name
  role          = aws_iam_role.embed_lambda.arn
  image_uri     = "${aws_ecr_repository.embed_document.repository_url}:latest"
  package_type  = "Image"
   
  
  timeout     = 300
  memory_size = 512

  depends_on = [aws_cloudwatch_log_group.embed_lambda]
}

resource "aws_lambda_permission" "embed_s3" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.embed_document.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.documents.arn
}
