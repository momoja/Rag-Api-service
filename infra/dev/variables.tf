variable "region" {
  description = "AWS region for all resources"
  type        = string
}

variable "project" {
  description = "Project name; prefixes all resource names"
  type        = string
  default     = "rag-agent"
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)"
  type        = string
  default     = "dev"
}

variable "alarm_email" {
  description = "Optional address subscribed to the alarm SNS topic (empty = no subscription)"
  type        = string
  default     = ""
}
