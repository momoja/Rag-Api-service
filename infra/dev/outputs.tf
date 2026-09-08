output "documents_bucket_name" {
  description = "S3 bucket for raw document uploads"
  value       = aws_s3_bucket.documents.id
}

output "api_invoke_url" {
  description = "Invoke URL of the presign HTTP API ($default stage)"
  value       = aws_apigatewayv2_api.presign.api_endpoint
}
