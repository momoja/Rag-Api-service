# Alarms over the pipeline's failure channels (Chapter 12).
#
# Logs are for diagnosis; alarms are for noticing. These cover the ways the
# pipeline breaks without an obvious symptom: a function erroring or being
# throttled, an event parked in a dead-letter queue (dlq.tf), the API returning
# 5xx, or the vector store filling up or saturating.
#
# Every alarm publishes to one SNS topic. `alarm_email` is optional and empty
# by default: topic and alarms still exist (console + metric history), nothing
# is mailed, and no apply depends on a human address being configured.
#
# Cost: ~$0.10 per alarm-month (~$2/mo at this count) plus SNS notifications.
#
# Deliberately absent for now: a "pipeline stalled" alarm (a composite of
# ingest successes, which needs a metric the pipeline does not emit yet) and
# latency/p95 alarms (nothing to tune against before real traffic).

locals {
  monitored_functions = {
    presign  = aws_lambda_function.presign_document
    ingest   = aws_lambda_function.ingest_document
    embed    = aws_lambda_function.embed_document
    index    = aws_lambda_function.index_document
    retrieve = aws_lambda_function.retrieve_document
  }
}

resource "aws_sns_topic" "alarms" {
  name = "${var.project}-${var.environment}-alarms"
}

resource "aws_sns_topic_subscription" "alarm_email" {
  count = var.alarm_email == "" ? 0 : 1

  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

# --- function health ---------------------------------------------------------

# Any error at all: for these functions an error is either a transient failure
# on its way to the DLQ or a misconfiguration — both worth an alarm.
resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  for_each = local.monitored_functions

  alarm_name          = "${var.project}-${var.environment}-${each.key}-errors"
  alarm_description   = "${each.key}-document raised errors; check its log group and the DLQ"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = each.value.function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

# Throttling means the account/region concurrency limit was hit: the events
# were retried by the platform, but the pipeline is behind.
resource "aws_cloudwatch_metric_alarm" "lambda_throttles" {
  for_each = local.monitored_functions

  alarm_name          = "${var.project}-${var.environment}-${each.key}-throttles"
  alarm_description   = "${each.key}-document was throttled (concurrency limit reached)"
  namespace           = "AWS/Lambda"
  metric_name         = "Throttles"
  dimensions          = { FunctionName = each.value.function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

# --- parked events -----------------------------------------------------------

# Anything in a DLQ is a document that did not finish the pipeline. ok_actions
# is set here (and only here): knowing the queue was drained again matters.
resource "aws_cloudwatch_metric_alarm" "dlq_depth" {
  for_each = aws_sqs_queue.dead_letter

  alarm_name          = "${var.project}-${var.environment}-${each.key}-dlq-not-empty"
  alarm_description   = "failed ${each.key} events are parked in ${each.value.name}; see the replay procedure in docs/STATUS.md"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = each.value.name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
}

# --- API and store -----------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "api_5xx" {
  alarm_name        = "${var.project}-${var.environment}-api-5xx"
  alarm_description = "the query API returned 5xx (handler, DB, or Bedrock failure)"
  namespace         = "AWS/ApiGateway"
  metric_name       = "5xx"
  dimensions = {
    ApiId = aws_apigatewayv2_api.presign.id
    Stage = aws_apigatewayv2_stage.default.name
  }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

# Vector store capacity: 20 GiB allocated, so 2 GiB free is the warning line
# well before writes start failing.
resource "aws_cloudwatch_metric_alarm" "db_free_storage" {
  alarm_name          = "${var.project}-${var.environment}-db-free-storage"
  alarm_description   = "less than 2 GiB free on the pgvector instance"
  namespace           = "AWS/RDS"
  metric_name         = "FreeStorageSpace"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.index.identifier }
  statistic           = "Minimum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 2147483648
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

# Sustained CPU saturation on a t4g.micro: queries slow down before they fail,
# so 15 minutes above 80% is worth knowing about (10-minute average of 3x5min).
resource "aws_cloudwatch_metric_alarm" "db_cpu" {
  alarm_name          = "${var.project}-${var.environment}-db-cpu"
  alarm_description   = "pgvector instance CPU above 80% for 15 minutes"
  namespace           = "AWS/RDS"
  metric_name         = "CPUUtilization"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.index.identifier }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  threshold           = 80
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}
