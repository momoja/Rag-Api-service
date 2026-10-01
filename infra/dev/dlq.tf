

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

 
  message_retention_seconds  = 1209600
  visibility_timeout_seconds = 60

  
 
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
