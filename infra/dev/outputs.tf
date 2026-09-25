output "documents_bucket_name" {
  description = "S3 bucket for raw document uploads"
  value       = aws_s3_bucket.documents.id
}

output "api_invoke_url" {
  description = "Invoke URL of the presign HTTP API ($default stage)"
  value       = aws_apigatewayv2_api.presign.api_endpoint
}

output "cognito_user_pool_id" {
  description = "Cognito user pool backing API auth (create users with admin-create-user)"
  value       = aws_cognito_user_pool.users.id
}

output "cognito_client_id" {
  description = "Cognito app client id: the JWT audience, needed to fetch a token"
  value       = aws_cognito_user_pool_client.app.id
}
