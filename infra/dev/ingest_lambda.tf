# ingest-document Lambda (Chapter 6) — the ingestion pipeline trigger.
#
# Container-image deployment mirrors presign-document (lambda.tf): image
# built/pushed from lambda/ingest_document/ (see its Dockerfile); deploy
# order is infra apply (repo first), push image, apply again.
#
# Chapter 6 addition over the Chapter 4 pattern: an S3 bucket notification
# fans object-created events under uploads/ into this function, which runs
# extract -> clean -> chunk -> stage (rag_agent.ingest) and writes results
# under processed/ in the same bucket. The notification is prefix-filtered
# to uploads/, so the pipeline's own processed/ writes never retrigger it.

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
  # Read raw uploads (keys presign minted, ch5) ...
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.documents.arn}/uploads/*"]
  }
  # ... and stage pipeline output in the same bucket. processed/ is outside
  # the notification filter, so these writes cannot retrigger ingestion.
  statement {
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.documents.arn}/processed/*"]
  }
  # Failed async invocations are parked in this function's dead-letter queue
  # (dlq.tf); Lambda's destination write is authorized by the role.
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
  # Text extraction (pypdf) is the heaviest step in the pipeline; 256 MiB
  # and 60s leave headroom over presign's 128 MiB/30s for larger PDFs.
  timeout     = 60
  memory_size = 256

  # No environment block: unlike presign, this function learns its bucket
  # from each S3 event record — no deployment config to drift or go stale.

  # Function creation races the log group; group pre-exists on deploy.
  depends_on = [aws_cloudwatch_log_group.ingest_lambda]
}

# S3 -> Lambda event sources. One notification resource carries all three
# targets (a bucket allows exactly one aws_s3_bucket_notification):
# 1. uploads/* (any suffix) -> ingest-document: raw document uploads, the
#    keys presign mints (ch5/ch6).
# 2. processed/*.jsonl -> embed-document: each document's chunks.jsonl
#    (ch7); the .jsonl suffix excludes metadata.json.
# 3. embedded/*.jsonl -> index-document: each document's embeddings.jsonl
#    (ch8) -> pgvector (rag_chunks in the RDS store, vector_store.tf).
#    Every stage's own writes land outside the other stages' filters, so
#    no stage can retrigger the pipeline.
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

  # Without these, S3 is not allowed to invoke the functions and events
  # silently never reach the pipeline (notifications go nowhere).
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
