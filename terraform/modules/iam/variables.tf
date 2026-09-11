variable "cluster_name" {
  type = string
}

variable "secret_arns" {
  type = list(string)
}

variable "ecr_repository_arns" {
  type = list(string)
}

variable "github_repository" {
  type = string

  validation {
    condition     = var.github_repository == "damir254/EKS-opentelemetry"
    error_message = "This publishing role is restricted to damir254/EKS-opentelemetry."
  }
}

variable "github_owner_id" {
  type = string
}

variable "github_repository_id" {
  type = string
}

variable "github_use_immutable_subject" {
  type = bool
}

variable "github_oidc_provider_arn" {
  type = string
}
