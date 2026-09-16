# Dead-letter queues for the asynchronous pipeline (Chapter 12).
#
# ingest/embed/index are invoked ASYNCHRONOUSLY by S3 notifications. Their
# handlers re-raise transient failures deliberately so the event is retried —
# but once the retries are exhausted Lambda drops the event silently: no row,
# no file, no trace, and that document stays half-ingested forever. An
# on-failure destination is the fix: the raw event lands in SQS, where it can
# be inspected and replayed (procedure in docs/STATUS.md).
#
# Retry policy is pinned here rather than left implicit: 2 retries (the async
# maximum) and a 1-hour event-age cap, so a backlog cannot burn Lambda time on
# an event whose S3 object may be long gone.
#
# The API functions (presign, retrieve) get no queue: API Gateway invokes them
# SYNCHRONOUSLY, so there is no retry and no destination — the caller owns the
# failure and receives the 500 the handler returns.

locals {
  async_functions = {
    ingest = aws_lambda_function.ingest_document
    embed  = aws_lambda_function.embed_document
    index  = aws_lambda_function.index_document
  }
}

resource "aws_sqs_queue" "dead_letter" {
  for_each = local.async_functions

  name = "${var.project}-${var.environment}-${each.key}-dlq"

  # 14 days (the SQS maximum): long enough to notice, bounded so old failures
  # expire instead of accumulating forever.
  message_retention_seconds  = 1209600
  visibility_timeout_seconds = 60

  # SSE-SQS (AWS-managed key) encrypts the parked events at rest without a KMS
  # key to manage or KMS permissions to grant the function roles.
  sqs_managed_sse_enabled = true
}

resource "aws_lambda_function_event_invoke_config" "async" {
  for_each = local.async_functions

  function_name                = each.value.function_name
  maximum_retry_attempts       = 2
  maximum_event_age_in_seconds = 3600

  destination_config {
    on_failure {
      destination = aws_sqs_queue.dead_letter[each.key].arn
    }
  }
}
