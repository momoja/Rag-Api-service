# VPC endpoints (Chapter 9 fix-up, discovered while wiring retrieval).
#
# VPC-attached Lambdas (index-document ch8, retrieve-document ch9) cannot
# reach public AWS APIs: the default VPC's subnets have no NAT gateway, and
# Lambda ENIs get no public IP. Without these endpoints, index-document
# would fail at startup reading its secret, and retrieve-document could not
# call Bedrock for query embeddings. Applied as part of Chapter 9 so the
# apply-time configuration is complete for both functions.
#
# Cost: S3 gateway endpoint is free; each interface endpoint is ~$0.01/hr
# (~$7/mo) — two of them (Secrets Manager, Bedrock Runtime).

locals {
  vpc_function_security_groups = [
    aws_security_group.index_lambda.id,
    aws_security_group.retrieve_lambda.id,
  ]
}

# The default VPC's main route table (gateway endpoints attach to routes).
data "aws_route_tables" "default_main" {
  vpc_id = data.aws_vpc.default.id

  filter {
    name   = "association.main"
    values = ["true"]
  }
}

# S3 — Gateway type: free, no hourly cost; serves index-document's reads of
# embedded/*.jsonl.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = data.aws_vpc.default.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = data.aws_route_tables.default_main.ids
}

# Secrets Manager — interface endpoint; index + retrieve both read SECRET_ARN.
resource "aws_vpc_endpoint" "secretsmanager" {
  vpc_id              = data.aws_vpc.default.id
  service_name        = "com.amazonaws.${var.region}.secretsmanager"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = data.aws_subnets.default.ids
  security_group_ids  = local.vpc_function_security_groups
  private_dns_enabled = true
}

# Bedrock Runtime — interface endpoint; retrieve-document embeds queries.
resource "aws_vpc_endpoint" "bedrock_runtime" {
  vpc_id              = data.aws_vpc.default.id
  service_name        = "com.amazonaws.${var.region}.bedrock-runtime"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = data.aws_subnets.default.ids
  security_group_ids  = local.vpc_function_security_groups
  private_dns_enabled = true
}
