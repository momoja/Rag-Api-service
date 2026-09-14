# Vector store + index-document Lambda (Chapter 8).
#
# Managed Postgres (RDS) running the pgvector extension — the 1024-dim
# contract (decisions 5/14) is enforced by the schema the code creates
# (rag_agent.vector.ensure_schema: vector(1024) column + HNSW index).
# RDS over Aurora: t4g.micro is ~5-10x cheaper than Aurora serverless v2's
# 0.5-ACU floor, and there is no pause/resume surprise. Chosen over
# OpenSearch Serverless for cost and local parity (same SQL via the compose
# pgvector container); revisit when scale demands k-NN at OCU granularity.
#
# The index-document Lambda must reach the database, so unlike the other
# functions it runs INSIDE the default VPC (private networking: RDS
# publicly_accessible = false, traffic only from the Lambda's SG on 5432).
# Credentials never appear in function env: RDS master password is generated
# once and stored in AWS Secrets Manager; the function reads SECRET_ARN at
# startup (see lambda/index_document/index_handler.py). Bedrock/S3-side
# functions do not need this file's resources.

locals {
  index_function_name = "${var.project}-${var.environment}-index-document"
  index_log_group     = "/aws/lambda/${local.index_function_name}"
  index_db_name       = "rag"
  index_db_user       = "rag"
}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# --- Networking -------------------------------------------------------------

resource "aws_security_group" "index_lambda" {
  name        = "${var.project}-${var.environment}-index-lambda"
  description = "Egress for index-document Lambda (VPC-attached)"
  vpc_id      = data.aws_vpc.default.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "index_db" {
  name        = "${var.project}-${var.environment}-index-db"
  description = "pgvector database: only the pipeline Lambdas may connect"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [
      aws_security_group.index_lambda.id,    # writes vectors (ch8)
      aws_security_group.retrieve_lambda.id, # reads them for search/answer (ch9/ch10)
    ]
  }
}

# --- Database + secret ------------------------------------------------------

resource "random_password" "index_db" {
  length  = 24
  special = false
}

resource "aws_db_subnet_group" "index" {
  name       = "${var.project}-${var.environment}-index"
  subnet_ids = data.aws_subnets.default.ids
}

resource "aws_db_instance" "index" {
  identifier     = "${var.project}-${var.environment}-vectors"
  engine         = "postgres"
  engine_version = "16.4"
  instance_class = "db.t4g.micro"

  db_name  = local.index_db_name
  username = local.index_db_user
  password = random_password.index_db.result

  allocated_storage      = 20
  storage_type           = "gp3"
  db_subnet_group_name   = aws_db_subnet_group.index.name
  vpc_security_group_ids = [aws_security_group.index_db.id]
  publicly_accessible    = false
  multi_az               = false

  # Dev posture: no replication, no snapshots to leak or bill; teardown is
  # a deliberate act at the apply session, like force_destroy=false on S3.
  skip_final_snapshot     = true
  backup_retention_period = 0
  deletion_protection     = false

  tags = { Name = "RAG pgvector store (${var.environment})" }
}

resource "aws_secretsmanager_secret" "index_db" {
  name = "${var.project}/${var.environment}/vectors-db"
}

resource "aws_secretsmanager_secret_version" "index_db" {
  secret_id = aws_secretsmanager_secret.index_db.id
  secret_string = jsonencode({
    host     = aws_db_instance.index.address
    port     = 5432
    dbname   = local.index_db_name
    username = local.index_db_user
    password = random_password.index_db.result
  })
}

# --- index-document Lambda ----------------------------------------------------

resource "aws_ecr_repository" "index_document" {
  name                 = "${var.project}-${var.environment}/index-document"
  image_tag_mutability = "MUTABLE"
  force_delete         = false

  image_scanning_configuration {
    scan_on_push = true
  }
}

data "aws_iam_policy_document" "index_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "index_role_policy" {
  # CloudWatch logs, scoped to this function's own log group only.
  statement {
    actions   = ["logs:CreateLogGroup"]
    resources = ["arn:aws:logs:${var.region}:${local.account_id}:log-group:${local.index_log_group}"]
  }
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.region}:${local.account_id}:log-group:${local.index_log_group}:log-stream:*"]
  }
  # Read staged embeddings (written by embed-document, ch7).
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.documents.arn}/embedded/*"]
  }
  # Read the DB secret once at startup. Scoped to our secret only.
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.index_db.arn]
  }
}

resource "aws_iam_role" "index_lambda" {
  name               = local.index_function_name
  assume_role_policy = data.aws_iam_policy_document.index_assume_role.json
}

resource "aws_iam_role_policy" "index_lambda" {
  name   = "index-document"
  role   = aws_iam_role.index_lambda.id
  policy = data.aws_iam_policy_document.index_role_policy.json
}

# VPC-attached Lambdas need ENI management; AWS-managed policy provides it.
resource "aws_iam_role_policy_attachment" "index_lambda_vpc" {
  role       = aws_iam_role.index_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_cloudwatch_log_group" "index_lambda" {
  name              = local.index_log_group
  retention_in_days = 7
}

resource "aws_lambda_function" "index_document" {
  function_name = local.index_function_name
  role          = aws_iam_role.index_lambda.arn
  image_uri     = "${aws_ecr_repository.index_document.repository_url}:latest"
  package_type  = "Image"
  timeout       = 60
  memory_size   = 256

  vpc_config {
    subnet_ids         = data.aws_subnets.default.ids
    security_group_ids = [aws_security_group.index_lambda.id]
  }

  environment {
    variables = {
      SECRET_ARN = aws_secretsmanager_secret.index_db.arn
    }
  }

  depends_on = [aws_cloudwatch_log_group.index_lambda]
}

resource "aws_lambda_permission" "index_s3" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.index_document.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.documents.arn
}
