
locals {
  ingest_function_name = "${var.project}-${var.environment}-ingest-document"
  ingest_log_group     = "/aws/lambda/${local.ingest_function_name}"
}

resource "aws_ecr_repository" "ingest_document" {
  name                 = "${var.project}-${var.environment}/ingest-document"
  image_tag_mutability = "MUTABLE" # dev: ':latest' overwritten on each push
  force_delete         = false

  image_scanning_configuration {
    scan_on_push = true
  }
}

data "aws_iam_policy_document" "ingest_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "ingest_role_policy" {
  # CloudWatch logs, scoped to this function's own log group only.
  statement {
    actions   = ["logs:CreateLogGroup"]
    resources = ["arn:aws:logs:${var.region}:${local.account_id}:log-group:${local.ingest_log_group}"]
  }
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.region}:${local.account_id}:log-group:${local.ingest_log_group}:log-stream:*"]
  }
  
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.documents.arn}/uploads/*"]
  }
  
  statement {
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.documents.arn}/processed/*"]
  }
  
  statement {
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.dead_letter["ingest"].arn]
  }
}

resource "aws_iam_role" "ingest_lambda" {
  name               = local.ingest_function_name
  assume_role_policy = data.aws_iam_policy_document.ingest_assume_role.json
}

resource "aws_iam_role_policy" "ingest_lambda" {
  name   = "ingest-document"
  role   = aws_iam_role.ingest_lambda.id
  policy = data.aws_iam_policy_document.ingest_role_policy.json
}

resource "aws_cloudwatch_log_group" "ingest_lambda" {
  name              = local.ingest_log_group
  retention_in_days = 7
}

resource "aws_lambda_function" "ingest_document" {
  function_name = local.ingest_function_name
  role          = aws_iam_role.ingest_lambda.arn
  image_uri     = "${aws_ecr_repository.ingest_document.repository_url}:latest"
  package_type  = "Image"
  # Text extraction (pypdf) is the heaviest step in the pipeline; 

  timeout     = 60
  memory_size = 256


  depends_on = [aws_cloudwatch_log_group.ingest_lambda]
}

resource "aws_s3_bucket_notification" "documents_ingest" {
  bucket = aws_s3_bucket.documents.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.ingest_document.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "uploads/"
  }

  lambda_function {
    lambda_function_arn = aws_lambda_function.embed_document.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "processed/"
    filter_suffix       = ".jsonl"
  }

  lambda_function {
    lambda_function_arn = aws_lambda_function.index_document.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "embedded/"
    filter_suffix       = ".jsonl"
  }

  depends_on = [
    aws_lambda_permission.ingest_s3,
    aws_lambda_permission.embed_s3,
    aws_lambda_permission.index_s3,
  ]
}

resource "aws_lambda_permission" "ingest_s3" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingest_document.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.documents.arn
}
