# Raw document store. Chapter 6's ingestion pipeline reads uploads from here,
# chunks them, embeds them, and writes vectors to the (Chapter 8) vector store.

resource "aws_s3_bucket" "documents" {
  # Account suffix keeps the globally-unique name deterministic per account,
  # mirroring the reference repo's <name>-<ACCOUNT_ID>-<REGION> convention.
  bucket        = local.documents_bucket_name
  force_destroy = false # data protection: teardown must be a deliberate act

  tags = {
    Name = "Raw document uploads for RAG ingestion"
  }
}

resource "aws_s3_bucket_versioning" "documents" {
  bucket = aws_s3_bucket.documents.id
  versioning_configuration {
    # Versioned PUTs make re-uploads detectable as updates by the Chapter 6
    # S3-event ingestion trigger.
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256" # SSE-S3: no KMS key or extra cost at this stage
    }
  }
}

resource "aws_s3_bucket_public_access_block" "documents" {
  bucket = aws_s3_bucket.documents.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
