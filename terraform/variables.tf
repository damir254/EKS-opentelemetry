variable "region" {
  description = "AWS region used by Terraform, GitOps and GitHub Actions."
  type        = string
  default     = "eu-central-1"

  validation {
    condition     = var.region == "eu-central-1"
    error_message = "This portfolio configuration targets eu-central-1."
  }
}

variable "cluster_name" {
  description = "EKS cluster and project resource name prefix."
  type        = string
  default     = "otel-demo-eks"

  validation {
    condition     = can(regex("^[a-zA-Z][a-zA-Z0-9-]{0,34}$", var.cluster_name))
    error_message = "Use a cluster name of up to 35 letters, digits or hyphens beginning with a letter."
  }
}

variable "availability_zones" {
  description = "Two eu-central-1 availability zones, each with public and private subnets."
  type        = list(string)
  default     = ["eu-central-1a", "eu-central-1b"]

  validation {
    condition     = length(var.availability_zones) == 2 && length(distinct(var.availability_zones)) == 2 && alltrue([for az in var.availability_zones : can(regex("^eu-central-1[a-z]$", az))])
    error_message = "Supply exactly two distinct eu-central-1 availability zones."
  }
}

variable "single_nat_gateway" {
  description = "Use one NAT for temporary lower-cost testing. False keeps one NAT per AZ; true loses outbound AZ redundancy and can incur cross-AZ traffic charges."
  type        = bool
  default     = false
}

variable "kubernetes_version" {
  description = "Supported EKS minor version. AWS lists 1.36 in standard support through August 2, 2027 (verified September 10, 2026)."
  type        = string
  default     = "1.36"
}

variable "cluster_admin_role_arn" {
  description = "Existing IAM role ARN granted EKS cluster-admin through an access entry. Authenticate kubectl as this role when bootstrapping Argo CD."
  type        = string

  validation {
    condition     = can(regex("^arn:aws:iam::[0-9]{12}:role/.+$", var.cluster_admin_role_arn))
    error_message = "Provide an existing IAM role ARN, not a user, root or STS assumed-role session ARN."
  }
}

variable "metrics_server_version" {
  description = "Optional exact Metrics Server add-on version. Null resolves the latest version compatible with kubernetes_version during plan; inspect addon_versions output and pin it for repeatable deployments."
  type        = string
  default     = null
}

variable "ecr_force_delete" {
  description = "Explicit disposable-environment option to delete repositories containing images during destroy. False protects published images by requiring manual cleanup first."
  type        = bool
  default     = false
}

variable "secret_recovery_window_in_days" {
  description = "Secrets Manager deletion recovery window. Set 0 explicitly for disposable testing if immediate deletion/recreation is required. Secret values are always populated outside Terraform."
  type        = number
  default     = 7

  validation {
    condition     = var.secret_recovery_window_in_days == 0 || (var.secret_recovery_window_in_days >= 7 && var.secret_recovery_window_in_days <= 30 && floor(var.secret_recovery_window_in_days) == var.secret_recovery_window_in_days)
    error_message = "Use 0 for immediate deletion or an integer recovery window from 7 through 30 days."
  }
}

variable "github_owner_id" {
  description = "Numeric owner ID for damir254 from GitHub API, used in the current immutable OIDC subject format."
  type        = string

  validation {
    condition     = can(regex("^[0-9]+$", var.github_owner_id))
    error_message = "Supply the actual numeric GitHub owner ID."
  }
}

variable "github_repository_id" {
  description = "Numeric ID of the NEW damir254/EKS-opentelemetry repository from GitHub API."
  type        = string

  validation {
    condition     = can(regex("^[0-9]+$", var.github_repository_id))
    error_message = "Supply the actual numeric GitHub EKS repository ID."
  }
}

variable "github_use_immutable_subject" {
  description = "New repositories created from July 15, 2026 use owner/repository IDs in OIDC subjects. Set false only after verifying this repository still emits the legacy name-only subject."
  type        = bool
  default     = true
}

variable "github_oidc_provider_arn" {
  description = "Existing account-wide GitHub OIDC provider ARN, if one already exists. Null creates it; do not create duplicate providers for the same issuer."
  type        = string
  default     = null
}
