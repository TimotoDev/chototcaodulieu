variable "aws_region" {
  type        = string
  description = "AWS region for the AI platform."
  default     = "ap-southeast-1"
}

variable "project_name" {
  type        = string
  description = "Short project name used in resource names."
  default     = "timoto-ai"
}

variable "environment" {
  type        = string
  description = "Deployment environment name."
  default     = "dev"
}

variable "vpc_cidr" {
  type        = string
  description = "CIDR for the dedicated AI platform VPC."
  default     = "10.40.0.0/16"
}

variable "nat_gateway_count" {
  type        = number
  description = "Number of NAT gateways. Disabled for the cost-optimized public-subnet worker; private subnets are isolated."
  default     = 0

  validation {
    condition     = var.nat_gateway_count >= 0 && var.nat_gateway_count <= 2
    error_message = "nat_gateway_count must be between 0 and 2."
  }
}

variable "log_retention_days" {
  type        = number
  description = "CloudWatch log retention for platform logs."
  default     = 30
}

variable "force_destroy_artifacts_bucket" {
  type        = bool
  description = "Allow Terraform to delete non-empty artifact buckets. Keep false outside disposable environments."
  default     = false
}

variable "api_gateway_http_apis" {
  type = map(object({
    api_id = string
    stage  = string
  }))
  description = "HTTP APIs to monitor for 5xx responses. Keep only APIs owned by this platform."
  default = {
    android_waitlist_prod = {
      api_id = "9z0gogbo8f"
      stage  = "$default"
    }
    android_waitlist_staging = {
      api_id = "cp30k1yr07"
      stage  = "$default"
    }
    chotot_dashboard = {
      api_id = "y6cthyc6gh"
      stage  = "$default"
    }
  }
}

variable "lambda_functions_to_monitor" {
  type        = set(string)
  description = "Lambda function names to monitor for errors and throttles."
  default = [
    "chotot-weekly-refresh",
    "chotot-image-sync-producer",
    "chotot-daily-scrape",
    "timoto-android-waitlist-staging",
    "timoto-android-waitlist-prod",
    "chotot-dashboard-api",
    "chotot-image-sync-worker",
  ]
}

variable "incident_orchestrator_image_tag" {
  type        = string
  description = "Immutable ECR tag for the evidence-first incident orchestrator worker."
  default     = "incident-reviewer-20260916-v2"
}
