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

# Bucket CORS (decision 10): browser clients preflight their pre-signed PUT
# straight to S3 before uploading, so S3 must answer the OPTIONS request.
# API Gateway CORS (api_gateway.tf) only covers API calls, not the S3
# endpoint. Dev allow-all mirrors the API's posture; tightened when a real
# client origin exists. ETag is exposed so client JS can verify an upload.
resource "aws_s3_bucket_cors_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id

  cors_rule {
    allowed_methods = ["GET", "HEAD", "PUT"]
    allowed_origins = ["*"]
    allowed_headers = ["*"]
    expose_headers  = ["ETag"]
    max_age_seconds = 3600
  }
}
