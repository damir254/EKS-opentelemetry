variable "region" {
  description = "AWS region containing the state bucket."
  type        = string
  default     = "eu-central-1"
}

variable "state_bucket_name" {
  description = "Globally unique S3 bucket name"
  type        = string
}
