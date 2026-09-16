# embed-document Lambda (Chapter 7) — turns Chapter 6's staged chunks into
# vectors for the (Chapter 8) vector store.
#
# Container-image deployment mirrors the other functions (image built/pushed
# from lambda/embed_document/; deploy order is infra apply, push, re-apply).
#
# Chapter 7 addition over the Chapter 6 pattern: a second target on the S3
# bucket notification (see ingest_lambda.tf) fans object-created events
# under processed/ with suffix .jsonl — i.e. exactly each document's
# chunks.jsonl, never its metadata.json — into this function. It reads the
# chunk file, embeds every chunk with Bedrock Titan (rag_agent.embed), and
# stages embedded/<document_id>/embeddings.jsonl for Chapter 8. Its own
# writes live under embedded/, outside both notification filters, so the
# pipeline can never retrigger itself.

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
  # Read staged chunks (written by ingest-document, ch6) ...
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.documents.arn}/processed/*"]
  }
  # ... and stage vectors in the same bucket under embedded/ — outside both
  # notification filters, so these writes cannot retrigger anything.
  statement {
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.documents.arn}/embedded/*"]
  }
  # Bedrock Titan Text Embeddings V2 — the model ID/region pair is the
  # 1024-dimension contract (decision 5). Foundation-model ARNs carry no
  # account segment.
  statement {
    actions   = ["bedrock:InvokeModel"]
    resources = ["arn:aws:bedrock:${var.region}::foundation-model/amazon.titan-embed-text-v2:0"]
  }
  # Failed async invocations are parked in this function's dead-letter queue
  # (dlq.tf); Lambda's destination write is authorized by the role.
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
  # One Bedrock round-trip per chunk with retries; 512 MiB/5 min headroom
  # for multi-hundred-chunk documents. No environment block: the bucket and
  # model are fixed contracts (event records + module constants).
  timeout     = 300
  memory_size = 512

  # Function creation races the log group; group pre-exists on deploy.
  depends_on = [aws_cloudwatch_log_group.embed_lambda]
}

# Bedrock is invoked only from this function's IAM role; the S3 side needs
# its own permission — see aws_s3_bucket_notification.documents_ingest in
# ingest_lambda.tf, which now carries this function as a second target.
resource "aws_lambda_permission" "embed_s3" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.embed_document.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.documents.arn
}
