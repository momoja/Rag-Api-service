
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
    from_port = 5432
    to_port   = 5432
    protocol  = "tcp"
    security_groups = [
      aws_security_group.index_lambda.id,    # writes vectors (ch8)
      aws_security_group.retrieve_lambda.id, # reads them for search/answer (ch9/ch10)
    ]
  }
}

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
  
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.documents.arn}/embedded/*"]
  }
  
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.index_db.arn]
  }
  
  statement {
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.dead_letter["index"].arn]
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
