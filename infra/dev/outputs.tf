output "documents_bucket_name" {
  description = "S3 bucket for raw document uploads"
  value       = aws_s3_bucket.documents.id
}

output "documents_bucket_arn" {
  description = "ARN of the documents bucket"
  value       = aws_s3_bucket.documents.arn
}
